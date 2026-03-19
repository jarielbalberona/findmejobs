from __future__ import annotations

import logging
from pathlib import Path

import typer
from pydantic import ValidationError
from sqlalchemy import select

from findmejobs.config.loader import ensure_directories, load_app_config, load_profile_config, load_source_configs, resolve_profile_config_path
from findmejobs.db.models import JobCluster, NormalizedJob
from findmejobs.db.repositories import (
    create_pipeline_run,
    finish_pipeline_run,
    upsert_job_score,
    upsert_profile,
    upsert_rank_model,
)
from findmejobs.db.session import create_session_factory
from findmejobs.domain.job import CanonicalJob
from findmejobs.ingestion.orchestrator import run_ingest
from findmejobs.observability.doctor import check_profile_config_health, run_doctor
from findmejobs.observability.logging import configure_logging
from findmejobs.profile_bootstrap.service import ProfileBootstrapService
from findmejobs.ranking.engine import rank_job
from findmejobs.review.service import export_review_packets, import_review_packets
from findmejobs.utils.ids import new_id
from findmejobs.utils.locking import FileLock

app = typer.Typer(help="Single-host job intelligence CLI")
review_app = typer.Typer(help="Review packet commands")
profile_app = typer.Typer(help="Profile bootstrap commands")
app.add_typer(review_app, name="review")
app.add_typer(profile_app, name="profile")

LOGGER = logging.getLogger(__name__)


def _load_runtime(app_config_path: Path, profile_path: Path, sources_dir: Path):
    app_config = load_app_config(app_config_path)
    profile = load_profile_config(resolve_profile_config_path(profile_path))
    sources = load_source_configs(sources_dir)
    ensure_directories(
        [
            app_config.storage.root_dir,
            app_config.storage.raw_dir,
            app_config.storage.review_outbox_dir,
            app_config.storage.review_inbox_dir,
            app_config.storage.lock_dir,
        ]
    )
    configure_logging(app_config.logging.level)
    session_factory = create_session_factory(app_config.database.url)
    return app_config, profile, sources, session_factory


def _pipeline_lock_path(app_config) -> Path:
    return app_config.storage.lock_dir / "pipeline.lock"


def _canonical_job_from_row(row: NormalizedJob) -> CanonicalJob:
    return CanonicalJob(
        source_job_id=row.source_job_id,
        source_id="",
        source_job_key="",
        canonical_url=row.canonical_url,
        company_name=row.company_name,
        title=row.title,
        location_text=row.location_text,
        location_type=row.location_type,
        country_code=row.country_code,
        city=row.city,
        region=row.region,
        seniority=row.seniority,
        employment_type=row.employment_type,
        salary_min=row.salary_min,
        salary_max=row.salary_max,
        salary_currency=row.salary_currency,
        salary_period=row.salary_period,
        description_text=row.description_text,
        tags=row.tags_json,
        posted_at=row.posted_at,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        normalization_errors=row.normalization_errors_json,
    )


@app.command()
def ingest(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, _profile, sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "ingest", new_id)
            session.commit()
            try:
                counts = run_ingest(session, app_config, sources, new_id)
                if counts["failed_sources"] > 0:
                    error_message = f"{counts['failed_sources']} source(s) failed during ingest"
                    finish_pipeline_run(run, "failed", counts, error_message=error_message)
                    session.commit()
                    typer.echo(f"ingest failed: {counts}")
                    raise typer.Exit(code=1)
                finish_pipeline_run(run, "success", counts)
                session.commit()
                typer.echo(f"ingest complete: {counts}")
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@app.command()
def rank(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "rank", new_id)
            session.commit()
            try:
                profile_row = upsert_profile(session, profile, new_id)
                rank_model = upsert_rank_model(session, profile, new_id)
                clusters = session.execute(
                    select(JobCluster, NormalizedJob)
                    .join(NormalizedJob, NormalizedJob.id == JobCluster.representative_job_id)
                    .where(NormalizedJob.normalization_status == "valid")
                )
                scored = 0
                for cluster, job_row in clusters:
                    breakdown = rank_job(_canonical_job_from_row(job_row), profile)
                    upsert_job_score(session, cluster.id, profile_row.id, rank_model.id, breakdown, new_id)
                    scored += 1
                finish_pipeline_run(run, "success", {"scored": scored})
                session.commit()
                typer.echo(f"rank complete: scored={scored}")
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@review_app.command("export")
def review_export(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "review_export", new_id)
            session.commit()
            try:
                exported = export_review_packets(session, app_config, profile, new_id)
                finish_pipeline_run(run, "success", {"exported": exported})
                session.commit()
                typer.echo(f"review export complete: exported={exported}")
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@review_app.command("import-results")
def review_import_results(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "review_import", new_id)
            session.commit()
            try:
                imported = import_review_packets(session, app_config, new_id)
                finish_pipeline_run(run, "success", {"imported": imported})
                session.commit()
                typer.echo(f"review import complete: imported={imported}")
            except typer.Exit:
                raise
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@app.command()
def doctor(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    try:
        app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"doctor failed: ['invalid_profile_config:{exc}']")
        raise typer.Exit(code=1)
    with session_factory() as session:
        errors = run_doctor(
            session,
            app_config.database.url,
            [
                app_config.storage.root_dir,
                app_config.storage.raw_dir,
                app_config.storage.review_outbox_dir,
                app_config.storage.review_inbox_dir,
                app_config.storage.lock_dir,
            ],
        )
    errors.extend(check_profile_config_health(profile_path.parent))
    if errors:
        typer.echo(f"doctor failed: {errors}")
        raise typer.Exit(code=1)
    typer.echo("doctor ok")


def _profile_service(state_root: Path, config_root: Path) -> ProfileBootstrapService:
    return ProfileBootstrapService(state_root=state_root, config_root=config_root, id_factory=new_id)


@profile_app.command("import")
def profile_import(
    file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    text: str | None = typer.Option(None),
    answers_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    answers_text: str | None = typer.Option(None),
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        resolved_answers = _resolve_answers_text(answers_file, answers_text)
        if file is None and text is None:
            metadata = service.refresh_pending_import(refinement_answers=resolved_answers)
        else:
            metadata = service.import_resume(
                file_path=file,
                pasted_text=text,
                reimport=False,
                refinement_answers=resolved_answers,
            )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        typer.echo(f"profile import failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"profile import complete: import_id={metadata.import_id} pending={metadata.extraction_pending}")


@profile_app.command("reimport")
def profile_reimport(
    file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    text: str | None = typer.Option(None),
    answers_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    answers_text: str | None = typer.Option(None),
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        resolved_answers = _resolve_answers_text(answers_file, answers_text)
        if file is None and text is None:
            metadata = service.refresh_pending_import(refinement_answers=resolved_answers)
        else:
            metadata = service.import_resume(
                file_path=file,
                pasted_text=text,
                reimport=True,
                refinement_answers=resolved_answers,
            )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        typer.echo(f"profile reimport failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"profile reimport complete: import_id={metadata.import_id} pending={metadata.extraction_pending}")


@profile_app.command("show-draft")
def profile_show_draft(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        profile = service.load_profile_draft()
    except FileNotFoundError as exc:
        typer.echo(f"profile show-draft failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(profile.model_dump_json(indent=2))


@profile_app.command("missing")
def profile_missing(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        report = service.load_missing_fields()
    except FileNotFoundError as exc:
        typer.echo(f"profile missing failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(report.model_dump_json(indent=2))


@profile_app.command("validate-draft")
def profile_validate_draft(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        errors = service.validate_draft()
    except FileNotFoundError as exc:
        typer.echo(f"profile validate-draft failed: {exc}")
        raise typer.Exit(code=1)
    if errors:
        typer.echo(f"profile draft invalid: {errors}")
        raise typer.Exit(code=1)
    typer.echo("profile draft valid")


@profile_app.command("diff")
def profile_diff(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        diff = service.diff_draft()
    except FileNotFoundError as exc:
        typer.echo(f"profile diff failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(diff.model_dump_json(indent=2))


@profile_app.command("promote-draft")
def profile_promote_draft(
    state_root: Path = typer.Option(Path("state/profile_bootstrap")),
    config_root: Path = typer.Option(Path("config")),
) -> None:
    service = _profile_service(state_root, config_root)
    try:
        diff = service.promote_draft()
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"profile promote-draft failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"profile draft promoted: safe_updates={len(diff.safe_auto_updates)}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()


def _resolve_answers_text(answers_file: Path | None, answers_text: str | None) -> str | None:
    if answers_file is not None and answers_text is not None:
        raise ValueError("provide either answers_file or answers_text")
    if answers_file is not None:
        return answers_file.read_text(encoding="utf-8")
    return answers_text
