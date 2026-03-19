from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from findmejobs.config.loader import load_profile_config
from findmejobs.db.models import DeliveryEvent, Digest, OpenClawReview, PipelineRun, ReviewPacket, Source, SourceFetchRun
from findmejobs.domain.source import PH_BOARD_KINDS
from findmejobs.db.session import database_file_from_url, fetch_pragma
from findmejobs.utils.time import ensure_utc, utcnow


def run_doctor(session: Session, database_url: str, required_paths: list[Path]) -> list[str]:
    errors: list[str] = []
    db_path = database_file_from_url(database_url)
    if db_path is not None and not db_path.exists():
        errors.append("database_missing")
    if fetch_pragma(session, "journal_mode") != "wal":
        errors.append("sqlite_wal_disabled")
    if fetch_pragma(session, "foreign_keys") != "1":
        errors.append("sqlite_foreign_keys_disabled")
    for path in required_paths:
        if not path.exists():
            errors.append(f"missing_path:{path}")
    _check_migration_state(session, errors)
    enabled_sources = session.scalar(select(func.count()).select_from(Source).where(Source.enabled.is_(True)))
    if enabled_sources in (None, 0):
        errors.append("no_enabled_sources")
    _check_pipeline_health(session, errors)
    _check_source_failures(session, errors)
    _check_review_backlog(session, errors)
    _check_delivery_health(session, errors)
    return errors


def check_profile_config_health(config_root: Path) -> list[str]:
    errors: list[str] = []
    profile_yaml = config_root / "profile.yaml"
    ranking_yaml = config_root / "ranking.yaml"
    if profile_yaml.exists() != ranking_yaml.exists():
        errors.append("canonical_profile_yaml_pair_incomplete")
        return errors
    if profile_yaml.exists():
        try:
            load_profile_config(profile_yaml)
        except Exception as exc:  # noqa: BLE001 - doctor should surface config failures without crashing
            errors.append(f"canonical_profile_yaml_invalid:{exc}")
    return errors


def _check_migration_state(session: Session, errors: list[str]) -> None:
    project_root = Path(__file__).resolve().parents[3]
    head_revision = ScriptDirectory(str(project_root / "alembic")).get_current_head()
    try:
        current_revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        current_revision = None
    if current_revision != head_revision:
        errors.append("migrations_out_of_date")


def _check_pipeline_health(session: Session, errors: list[str]) -> None:
    latest_success = session.scalar(
        select(PipelineRun).where(PipelineRun.status == "success").order_by(PipelineRun.finished_at.desc()).limit(1)
    )
    if latest_success is None or latest_success.finished_at is None:
        errors.append("pipeline_never_succeeded")
        return
    if (utcnow() - ensure_utc(latest_success.finished_at)).total_seconds() > 24 * 60 * 60:
        errors.append("pipeline_stale")


def _check_source_failures(session: Session, errors: list[str]) -> None:
    enabled_sources = session.scalars(select(Source).where(Source.enabled.is_(True))).all()
    for source in enabled_sources:
        recent_runs = session.scalars(
            select(SourceFetchRun)
            .where(SourceFetchRun.source_id == source.id)
            .order_by(SourceFetchRun.started_at.desc(), SourceFetchRun.id.desc())
            .limit(3)
        ).all()
        if len(recent_runs) < 3:
            continue
        if all(run.status == "failed" for run in recent_runs):
            errors.append(f"source_repeated_failures:{source.name}")
            continue
        if source.kind in PH_BOARD_KINDS and _has_partial_degradation(recent_runs):
            errors.append(f"source_partial_degradation:{source.name}")


def _check_review_backlog(session: Session, errors: list[str]) -> None:
    exported_without_review = session.scalar(
        select(func.count())
        .select_from(ReviewPacket)
        .outerjoin(OpenClawReview, OpenClawReview.review_packet_id == ReviewPacket.id)
        .where(ReviewPacket.status == "exported")
        .where(OpenClawReview.id.is_(None))
    )
    if exported_without_review and exported_without_review > 50:
        errors.append("review_backlog_high")


def _check_delivery_health(session: Session, errors: list[str]) -> None:
    latest_digest = session.scalar(select(Digest).order_by(Digest.sent_at.desc()).limit(1))
    if latest_digest is not None and latest_digest.status == "failed":
        errors.append("latest_digest_failed")
    recent_failed_deliveries = session.scalar(
        select(func.count())
        .select_from(DeliveryEvent)
        .where(DeliveryEvent.status == "failed")
        .where(DeliveryEvent.created_at >= utcnow() - timedelta(days=1))
    )
    if recent_failed_deliveries and recent_failed_deliveries > 3:
        errors.append("delivery_failures_high")


def _has_partial_degradation(runs: list[SourceFetchRun]) -> bool:
    degraded_runs = 0
    for run in runs:
        if run.status != "success":
            continue
        if run.raw_seen_count <= 0:
            continue
        if run.skipped_count <= 0:
            continue
        if (run.skipped_count / run.raw_seen_count) >= 0.2:
            degraded_runs += 1
    return degraded_runs >= 2
