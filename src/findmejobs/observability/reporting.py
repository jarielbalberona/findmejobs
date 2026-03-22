from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from findmejobs.config.models import QualityConfig
from findmejobs.db.models import ApplicationSubmission, DeliveryEvent, Digest, JobScore, PipelineRun, Source, SourceFetchRun
from findmejobs.domain.source import source_family_for_kind
from findmejobs.observability.doctor import evaluate_quality_gates


def build_report(session: Session, *, quality: QualityConfig | None = None) -> dict:
    quality = quality or QualityConfig()
    sources = []
    for source in session.scalars(select(Source).order_by(Source.name.asc())).all():
        latest = session.scalar(
            select(SourceFetchRun)
            .where(SourceFetchRun.source_id == source.id)
            .order_by(desc(SourceFetchRun.started_at), desc(SourceFetchRun.id))
            .limit(1)
        )
        sources.append(
            {
                "name": source.name,
                "kind": source.kind,
                "family": source_family_for_kind(source.kind),
                "enabled": source.enabled,
                "priority": source.priority,
                "trust_weight": source.trust_weight,
                "last_successful_run_at": source.last_successful_run_at.isoformat() if source.last_successful_run_at else None,
                "latest_status": latest.status if latest else None,
                "raw_seen": latest.raw_seen_count if latest else 0,
                "seen": latest.seen_count if latest else 0,
                "skipped": latest.skipped_count if latest else 0,
                "skip_ratio": _skip_ratio(latest),
                "inserted": latest.inserted_count if latest else 0,
                "updated": latest.updated_count if latest else 0,
                "failed": latest.failed_count if latest else 0,
                "parse_errors": latest.parse_error_count if latest else 0,
                "dedupe_merges": latest.dedupe_merge_count if latest else 0,
                "normalized_valid": latest.normalized_valid_count if latest else 0,
            }
        )

    latest_runs = [
        {
            "command": run.command,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "stats": run.stats_json,
        }
        for run in session.scalars(select(PipelineRun).order_by(desc(PipelineRun.started_at)).limit(10)).all()
    ]

    latest_digest = session.scalar(select(Digest).order_by(desc(Digest.sent_at)).limit(1))
    delivery_summary = {
        "latest_digest_status": latest_digest.status if latest_digest else None,
        "latest_digest_date": latest_digest.digest_date if latest_digest else None,
        "events": session.scalar(select(func.count()).select_from(DeliveryEvent)) or 0,
    }
    ranking_summary = {
        "ranked": session.scalar(select(func.count()).select_from(JobScore).where(JobScore.passed_hard_filters.is_(True))) or 0,
        "filtered": session.scalar(select(func.count()).select_from(JobScore).where(JobScore.passed_hard_filters.is_(False))) or 0,
    }
    ready_count = ranking_summary["ranked"]
    submitted_count = (
        session.scalar(
            select(func.count())
            .select_from(ApplicationSubmission)
            .where(ApplicationSubmission.status.in_(["submitted", "interview", "rejected", "offer", "withdrawn"]))
        )
        or 0
    )
    interview_count = session.scalar(
        select(func.count()).select_from(ApplicationSubmission).where(ApplicationSubmission.status == "interview")
    ) or 0
    reject_count = session.scalar(
        select(func.count()).select_from(ApplicationSubmission).where(ApplicationSubmission.status == "rejected")
    ) or 0
    offer_count = session.scalar(
        select(func.count()).select_from(ApplicationSubmission).where(ApplicationSubmission.status == "offer")
    ) or 0
    application_funnel = {
        "ready_count": ready_count,
        "submitted_count": submitted_count,
        "interview_count": interview_count,
        "reject_count": reject_count,
        "offer_count": offer_count,
        "interview_conversion_ratio": round(interview_count / submitted_count, 4) if submitted_count else 0.0,
        "offer_conversion_ratio": round(offer_count / submitted_count, 4) if submitted_count else 0.0,
    }
    return {
        "sources": sources,
        "pipeline_runs": latest_runs,
        "delivery": delivery_summary,
        "ranking": ranking_summary,
        "quality_gates": evaluate_quality_gates(session, quality),
        "application_funnel": application_funnel,
    }


def _skip_ratio(latest: SourceFetchRun | None) -> float:
    if latest is None or latest.raw_seen_count <= 0:
        return 0.0
    return round(latest.skipped_count / latest.raw_seen_count, 3)
