from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
from pydantic import ValidationError
from sqlalchemy import select

from findmejobs.application.service import ApplicationDraftService
from findmejobs.config.loader import ensure_directories, load_app_config, load_profile_config, load_source_configs, resolve_profile_config_path
from findmejobs.db.models import Digest, JobCluster, JobScore, NormalizedJob, Source, SourceJob
from findmejobs.db.repositories import (
    create_job_feedback,
    create_pipeline_run,
    finish_pipeline_run,
    upsert_job_score,
    upsert_profile,
    upsert_rank_model,
)
from findmejobs.db.session import create_session_factory
from findmejobs.delivery.digest import send_digest
from findmejobs.domain.job import CanonicalJob
from findmejobs.domain.source import SourceJobRecord
from findmejobs.feedback import ALLOWED_FEEDBACK_TYPES, feedback_types_for_job, record_feedback
from findmejobs.ingestion.orchestrator import run_ingest
from findmejobs.normalization.canonicalize import normalize_job
from findmejobs.observability.doctor import check_profile_config_health, run_doctor
from findmejobs.observability.logging import configure_logging
from findmejobs.observability.reporting import build_report
from findmejobs.profile_bootstrap.service import ProfileBootstrapService
from findmejobs.ranking.engine import rank_job_with_feedback
from findmejobs.review.service import export_review_packets, import_review_packets
from findmejobs.utils.ids import new_id
from findmejobs.utils.locking import FileLock
from findmejobs.utils.time import utcnow

app = typer.Typer(help="Single-host job intelligence CLI")
review_app = typer.Typer(help="Review packet commands")
profile_app = typer.Typer(help="Profile bootstrap commands")
digest_app = typer.Typer(help="Digest commands")
feedback_app = typer.Typer(help="Feedback commands")
reprocess_app = typer.Typer(help="Reprocess commands")
app.add_typer(review_app, name="review")
app.add_typer(profile_app, name="profile")
app.add_typer(digest_app, name="digest")
app.add_typer(feedback_app, name="feedback")
app.add_typer(reprocess_app, name="reprocess")

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


def _canonical_job_from_row(row: NormalizedJob, source: Source | None = None) -> CanonicalJob:
    return CanonicalJob(
        source_job_id=row.source_job_id,
        source_id=source.id if source is not None else "",
        source_job_key="",
        source_name=source.name if source is not None else None,
        source_trust_weight=source.trust_weight if source is not None else 1.0,
        source_priority=source.priority if source is not None else 0,
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
                    select(JobCluster, NormalizedJob, Source)
                    .join(NormalizedJob, NormalizedJob.id == JobCluster.representative_job_id)
                    .join(SourceJob, SourceJob.id == NormalizedJob.source_job_id)
                    .join(Source, Source.id == SourceJob.source_id)
                    .where(NormalizedJob.normalization_status == "valid")
                )
                scored = 0
                filtered = 0
                for cluster, job_row, source in clusters:
                    feedback_types = feedback_types_for_job(
                        session,
                        cluster_id=cluster.id,
                        company_name=job_row.company_name,
                        title=job_row.title,
                    )
                    breakdown = rank_job_with_feedback(_canonical_job_from_row(job_row, source), profile, feedback_types=feedback_types)
                    upsert_job_score(session, cluster.id, profile_row.id, rank_model.id, breakdown, new_id)
                    scored += 1
                    if breakdown.hard_filter_reasons:
                        filtered += 1
                finish_pipeline_run(run, "success", {"scored": scored, "filtered": filtered})
                session.commit()
                typer.echo(f"rank complete: scored={scored} filtered={filtered}")
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


@digest_app.command("send")
def digest_send(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    digest_date: str | None = typer.Option(None),
    dry_run: bool = typer.Option(False, help="Build digest without sending email"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "digest_send", new_id)
            session.commit()
            try:
                digest = send_digest(session, app_config, profile, id_factory=new_id, digest_date=digest_date, dry_run=dry_run)
                finish_pipeline_run(run, "success", {"digest_id": digest.id, "status": digest.status, "dry_run": dry_run})
                session.commit()
                if dry_run:
                    typer.echo(f"digest dry-run complete: digest_id={digest.id} items={len(digest.body_text.splitlines())}")
                    typer.echo(digest.body_text)
                else:
                    typer.echo(f"digest send complete: digest_id={digest.id} status={digest.status}")
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                typer.echo(f"digest send failed: {exc}")
                raise typer.Exit(code=1)


@digest_app.command("resend")
def digest_resend(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    digest_date: str = typer.Option(...),
    dry_run: bool = typer.Option(False, help="Build digest without sending email"),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            original = session.scalar(select(Digest).where(Digest.digest_date == digest_date).order_by(Digest.sent_at.desc()))
            if original is None:
                typer.echo(f"digest resend failed: no digest for {digest_date}")
                raise typer.Exit(code=1)
            run = create_pipeline_run(session, "digest_resend", new_id)
            session.commit()
            try:
                digest = send_digest(
                    session,
                    app_config,
                    profile,
                    id_factory=new_id,
                    digest_date=digest_date,
                    resend_of_digest_id=original.id,
                    dry_run=dry_run,
                )
                finish_pipeline_run(run, "success", {"digest_id": digest.id, "status": digest.status, "resend_of": original.id, "dry_run": dry_run})
                session.commit()
                if dry_run:
                    typer.echo(f"digest resend dry-run complete: digest_id={digest.id}")
                    typer.echo(digest.body_text)
                else:
                    typer.echo(f"digest resend complete: digest_id={digest.id} status={digest.status}")
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                typer.echo(f"digest resend failed: {exc}")
                raise typer.Exit(code=1)


@feedback_app.command("record")
def feedback_record(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    feedback_type: str = typer.Option(...),
    cluster_id: str | None = typer.Option(None),
    company_name: str | None = typer.Option(None),
    title_keyword: str | None = typer.Option(None),
    notes: str | None = typer.Option(None),
) -> None:
    if feedback_type not in ALLOWED_FEEDBACK_TYPES:
        typer.echo(f"feedback failed: invalid feedback type {feedback_type}")
        raise typer.Exit(code=1)
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            record = record_feedback(
                session,
                id_factory=new_id,
                feedback_type=feedback_type,
                cluster_id=cluster_id,
                company_name=company_name,
                title_keyword=title_keyword,
                notes=notes,
            )
            session.commit()
            typer.echo(f"feedback recorded: id={record.id} type={record.feedback_type}")


@app.command()
def rerank(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    rank(app_config_path=app_config_path, profile_path=profile_path, sources_dir=sources_dir)


@reprocess_app.command("review-packets")
def reprocess_review_packets(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    review_export(app_config_path=app_config_path, profile_path=profile_path, sources_dir=sources_dir)


@reprocess_app.command("normalize")
def reprocess_normalize(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    source_job_id: str = typer.Option(...),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            run = create_pipeline_run(session, "reprocess_normalize", new_id)
            session.commit()
            try:
                row = session.execute(
                    select(SourceJob, Source, NormalizedJob)
                    .join(Source, Source.id == SourceJob.source_id)
                    .join(NormalizedJob, NormalizedJob.source_job_id == SourceJob.id)
                    .where(SourceJob.id == source_job_id)
                ).first()
                if row is None:
                    raise ValueError(f"source_job_not_found:{source_job_id}")
                source_job, source, normalized = row
                record = _record_from_existing(source_job, source, normalized)
                canonical = normalize_job(source_job.id, source.id, source_job.seen_at, record)
                from findmejobs.db.repositories import upsert_normalized_job
                from findmejobs.dedupe.clustering import assign_job_cluster

                normalized_row, _created = upsert_normalized_job(session, canonical, new_id)
                assign_job_cluster(session, normalized_row, new_id)
                finish_pipeline_run(run, "success", {"source_job_id": source_job.id})
                session.commit()
                typer.echo(f"reprocess normalize complete: source_job_id={source_job.id}")
            except Exception as exc:
                finish_pipeline_run(run, "failed", error_message=str(exc))
                session.commit()
                typer.echo(f"reprocess normalize failed: {exc}")
                raise typer.Exit(code=1)


@app.command()
def report(
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
) -> None:
    app_config, _profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    with session_factory() as session:
        report_payload = build_report(session)
    typer.echo(json.dumps(report_payload, indent=2))


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


def _application_service(state_root: Path) -> ApplicationDraftService:
    return ApplicationDraftService(state_root=state_root)


@app.command("prepare-application")
def prepare_application(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                packet, missing_inputs = service.prepare_application(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"prepare-application failed: {exc}")
                raise typer.Exit(code=1)
    typer.echo(
        f"prepare-application complete: job_id={packet.job_id} "
        f"questions={len(packet.application_questions)} missing_inputs={len(missing_inputs)}"
    )


@app.command("draft-cover-letter")
def draft_cover_letter(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                draft = service.draft_cover_letter(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"draft-cover-letter failed: {exc}")
                raise typer.Exit(code=1)
    typer.echo(f"draft-cover-letter complete: job_id={draft.job_id} origin={draft.origin}")


@app.command("draft-answers")
def draft_answers(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                draft = service.draft_answers(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"draft-answers failed: {exc}")
                raise typer.Exit(code=1)
    typer.echo(f"draft-answers complete: job_id={draft.job_id} answers={len(draft.answers)} origin={draft.origin}")


@app.command("show-application")
def show_application(
    job_id: str = typer.Option(...),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    service = _application_service(state_root)
    try:
        payload = service.show_application(job_id=job_id)
    except FileNotFoundError as exc:
        typer.echo(f"show-application failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command("validate-application")
def validate_application(
    job_id: str = typer.Option(...),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    service = _application_service(state_root)
    with session_factory() as session:
        report = service.validate_application(session, profile, job_id=job_id)
    typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    if report.errors:
        raise typer.Exit(code=1)


@app.command("regenerate-application")
def regenerate_application(
    job_id: str = typer.Option(...),
    questions_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    app_config_path: Path = typer.Option(Path("config/app.toml"), exists=True),
    profile_path: Path = typer.Option(Path("config/profile.toml")),
    sources_dir: Path = typer.Option(Path("config/sources.d"), exists=True, file_okay=False),
    state_root: Path = typer.Option(Path("state/applications")),
) -> None:
    app_config, profile, _sources, session_factory = _load_runtime(app_config_path, profile_path, sources_dir)
    service = _application_service(state_root)
    with FileLock(_pipeline_lock_path(app_config)):
        with session_factory() as session:
            try:
                result = service.regenerate_application(
                    session,
                    profile,
                    job_id=job_id,
                    questions_file=questions_file,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"regenerate-application failed: {exc}")
                raise typer.Exit(code=1)
    typer.echo(json.dumps(result, indent=2))


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
        result = service.validate_draft()
    except FileNotFoundError as exc:
        typer.echo(f"profile validate-draft failed: {exc}")
        raise typer.Exit(code=1)
    if result.errors:
        typer.echo(f"profile draft invalid: status={result.status} errors={result.errors}")
        raise typer.Exit(code=1)
    typer.echo(f"profile draft valid: status={result.status}")


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


def _record_from_existing(source_job: SourceJob, source: Source, normalized: NormalizedJob) -> SourceJobRecord:
    payload = source_job.payload_json or {}
    return SourceJobRecord(
        source_job_key=source_job.source_job_key,
        source_url=source_job.source_url,
        apply_url=source_job.apply_url,
        title=str(payload.get("title") or normalized.title),
        company=str(payload.get("company") or normalized.company_name or source.name),
        location_text=str(payload.get("location_text") or normalized.location_text or ""),
        posted_at_raw=payload.get("published") or payload.get("posted_at") or normalized.posted_at.isoformat() if normalized.posted_at else None,
        employment_type_raw=payload.get("employment_type") or normalized.employment_type,
        seniority_raw=payload.get("seniority") or normalized.seniority,
        salary_raw=payload.get("salary") or payload.get("salary_raw"),
        description_raw=payload.get("summary") or payload.get("description") or normalized.description_text,
        tags_raw=payload.get("tags") or normalized.tags_json,
        raw_payload=payload,
    )
