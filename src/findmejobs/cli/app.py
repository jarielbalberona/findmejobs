from __future__ import annotations

import logging
from pathlib import Path

import typer
from sqlalchemy import select

from findmejobs.config.loader import ensure_directories, load_app_config, load_profile_config, load_source_configs
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
from findmejobs.observability.doctor import run_doctor
from findmejobs.observability.logging import configure_logging
from findmejobs.ranking.engine import rank_job
from findmejobs.review.service import export_review_packets, import_review_packets
from findmejobs.utils.ids import new_id
from findmejobs.utils.locking import FileLock

app = typer.Typer(help="Single-host job intelligence CLI")
review_app = typer.Typer(help="Review packet commands")
app.add_typer(review_app, name="review")

LOGGER = logging.getLogger(__name__)


def _load_runtime(app_config_path: Path, profile_path: Path, sources_dir: Path):
    app_config = load_app_config(app_config_path)
    profile = load_profile_config(profile_path)
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
    profile_path: Path = typer.Option(Path("config/profile.toml"), exists=True),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, _profile, sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(app_config.storage.lock_dir / "ingest.lock"):
        with session_factory() as session:
            run = create_pipeline_run(session, "ingest", new_id)
            session.commit()
            try:
                counts = run_ingest(session, app_config, sources, new_id)
                finish_pipeline_run(run, "success", counts)
                session.commit()
                typer.echo(f"ingest complete: {counts}")
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@app.command()
def rank(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml"), exists=True),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(app_config.storage.lock_dir / "rank.lock"):
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
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@review_app.command("export")
def review_export(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml"), exists=True),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(app_config.storage.lock_dir / "review-export.lock"):
        with session_factory() as session:
            run = create_pipeline_run(session, "review_export", new_id)
            session.commit()
            try:
                exported = export_review_packets(session, app_config, profile, new_id)
                finish_pipeline_run(run, "success", {"exported": exported})
                session.commit()
                typer.echo(f"review export complete: exported={exported}")
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@review_app.command("import-results")
def review_import_results(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml"), exists=True),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(app_config.storage.lock_dir / "review-import.lock"):
        with session_factory() as session:
            run = create_pipeline_run(session, "review_import", new_id)
            session.commit()
            try:
                imported = import_review_packets(session, app_config, new_id)
                finish_pipeline_run(run, "success", {"imported": imported})
                session.commit()
                typer.echo(f"review import complete: imported={imported}")
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                raise


@app.command()
def doctor(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml"), exists=True),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
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
    if errors:
        typer.echo(f"doctor failed: {errors}")
        raise typer.Exit(code=1)
    typer.echo("doctor ok")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
