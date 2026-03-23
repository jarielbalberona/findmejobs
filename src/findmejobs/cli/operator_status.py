"""Aggregate operator status for `findmejobs status`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.config.models import AppConfig

if TYPE_CHECKING:
    from findmejobs.config.models import ProfileConfig
from findmejobs.config.loader import load_source_configs
from findmejobs.db.models import Digest, PipelineRun
from findmejobs.observability.doctor import check_profile_config_health, run_doctor
from findmejobs.utils.time import utcnow

from findmejobs.cli.operator_queues import (
    count_application_draft_pending,
    count_review_eligible_jobs,
    count_review_packets_pending_import,
    count_review_packets_with_imported_results,
)


def _latest_pipeline(session: Session, command: str) -> dict[str, Any] | None:
    row = session.scalar(
        select(PipelineRun).where(PipelineRun.command == command).order_by(PipelineRun.started_at.desc()).limit(1)
    )
    if row is None:
        return None
    return {
        "id": row.id,
        "command": row.command,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "stats": row.stats_json or {},
        "error_message": row.error_message,
    }


def _latest_digest(session: Session) -> dict[str, Any] | None:
    row = session.scalar(select(Digest).order_by(Digest.sent_at.desc().nulls_last(), Digest.id.desc()).limit(1))
    if row is None:
        return None
    return {
        "id": row.id,
        "digest_date": row.digest_date,
        "status": row.status,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


def build_operator_status(
    session: Session,
    app_config: AppConfig,
    *,
    profile: ProfileConfig | None,
    app_config_path: Path,
    profile_path: Path,
    sources_path: Path,
    applications_state_root: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    config_ok = True
    try:
        sources = load_source_configs(sources_path)
        enabled_sources = sum(1 for s in sources if s.enabled)
    except Exception as exc:  # noqa: BLE001
        config_ok = False
        errors.append(f"sources_load_failed:{exc}")
        enabled_sources = 0

    profile_ready = False
    if profile is None:
        errors.append("profile_config_unavailable")
    else:
        profile_ready = True
        if not profile.target_titles:
            profile_ready = False
            warnings.append("profile_missing_target_titles")

    ranking_ready = True
    ranking_path = profile_path.with_name("ranking.yaml")
    if not ranking_path.exists():
        ranking_ready = False
        errors.append("ranking_yaml_missing")

    source_ready = enabled_sources > 0
    if not source_ready:
        warnings.append("no_enabled_sources_in_config")

    doctor_errors = run_doctor(
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
    doctor_errors.extend(check_profile_config_health(profile_path.parent))
    for e in doctor_errors:
        if e.startswith("pipeline_stale"):
            warnings.append(e)
        elif e in ("no_enabled_sources", "pipeline_never_succeeded"):
            warnings.append(e)
        else:
            errors.append(e)

    if profile is None:
        eligible = 0
        pending_import = 0
        imported = 0
        app_pending = 0
    else:
        eligible = count_review_eligible_jobs(session, profile)
        pending_import = count_review_packets_pending_import(session, profile)
        imported = count_review_packets_with_imported_results(session, profile)
        app_pending = count_application_draft_pending(session, profile, applications_state_root)

    ok = config_ok and profile_ready and ranking_ready and not errors

    return {
        "ok": ok,
        "config_valid": config_ok,
        "profile_ready": profile_ready,
        "ranking_ready": ranking_ready,
        "source_ready": source_ready,
        "enabled_source_count": enabled_sources,
        "latest_ingest": _latest_pipeline(session, "ingest"),
        "latest_rank": _latest_pipeline(session, "rank"),
        "latest_review_export": _latest_pipeline(session, "review_export"),
        "latest_review_import": _latest_pipeline(session, "review_import"),
        "latest_digest_send": _latest_digest(session),
        "review_eligible_job_count": eligible,
        "review_packets_pending_import_count": pending_import,
        "review_imported_result_count": imported,
        "application_draft_pending_count": app_pending,
        "warnings": warnings,
        "errors": errors,
        "paths": {
            "app_config": str(app_config_path.resolve()),
            "profile": str(profile_path.resolve()),
            "ranking": str(ranking_path.resolve()),
            "sources": str(sources_path.resolve()),
            "review_outbox": str(app_config.storage.review_outbox_dir.resolve()),
            "review_inbox": str(app_config.storage.review_inbox_dir.resolve()),
            "applications_state": str(applications_state_root.resolve()),
        },
        "meta": {"generated_at": utcnow().isoformat()},
    }
