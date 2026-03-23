"""Read-only queue / inspection queries for operator CLI."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from findmejobs.config.models import ProfileConfig
from findmejobs.db.models import (
    JobCluster,
    JobScore,
    NormalizedJob,
    OpenClawReview,
    Profile,
    RankModel,
    ReviewPacket,
    SourceJob,
    Source,
)
from findmejobs.observability.job_listing import fetch_job_previews


def _profile_rank_ids(session: Session, profile: ProfileConfig) -> tuple[str | None, str | None]:
    prow = session.scalar(select(Profile).where(Profile.version == profile.version))
    rrow = session.scalar(select(RankModel).where(RankModel.version == profile.rank_model_version))
    if prow is None or rrow is None:
        return None, None
    return prow.id, rrow.id


def count_review_eligible_jobs(session: Session, profile: ProfileConfig) -> int:
    pid, rid = _profile_rank_ids(session, profile)
    if pid is None or rid is None:
        return 0
    return (
        session.scalar(
            select(func.count())
            .select_from(JobScore)
            .where(JobScore.profile_id == pid)
            .where(JobScore.rank_model_id == rid)
            .where(JobScore.passed_hard_filters.is_(True))
            .where(JobScore.score_total >= profile.ranking.minimum_score)
        )
        or 0
    )


def count_review_packets_pending_import(session: Session, profile: ProfileConfig) -> int:
    """Exported packets for this profile/rank model that have no OpenClawReview row yet."""
    pid, rid = _profile_rank_ids(session, profile)
    if pid is None or rid is None:
        return 0
    return (
        session.scalar(
            select(func.count())
            .select_from(ReviewPacket)
            .join(JobScore, JobScore.id == ReviewPacket.job_score_id)
            .outerjoin(OpenClawReview, OpenClawReview.review_packet_id == ReviewPacket.id)
            .where(JobScore.profile_id == pid)
            .where(JobScore.rank_model_id == rid)
            .where(ReviewPacket.status == "exported")
            .where(OpenClawReview.id.is_(None))
        )
        or 0
    )


def count_review_packets_with_imported_results(session: Session, profile: ProfileConfig) -> int:
    pid, rid = _profile_rank_ids(session, profile)
    if pid is None or rid is None:
        return 0
    return (
        session.scalar(
            select(func.count())
            .select_from(OpenClawReview)
            .join(ReviewPacket, ReviewPacket.id == OpenClawReview.review_packet_id)
            .join(JobScore, JobScore.id == ReviewPacket.job_score_id)
            .where(JobScore.profile_id == pid)
            .where(JobScore.rank_model_id == rid)
        )
        or 0
    )


def fetch_review_queue_rows(session: Session, profile: ProfileConfig, *, limit: int) -> list[dict]:
    """Jobs eligible for review export that still need OpenClaw work (no review row)."""
    pid, rid = _profile_rank_ids(session, profile)
    if pid is None or rid is None:
        return []
    stmt = (
        select(JobCluster, JobScore, NormalizedJob, Source, ReviewPacket)
        .join(JobScore, JobScore.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobCluster.representative_job_id)
        .join(SourceJob, SourceJob.id == NormalizedJob.source_job_id)
        .join(Source, Source.id == SourceJob.source_id)
        .outerjoin(
            ReviewPacket,
            (ReviewPacket.cluster_id == JobCluster.id) & (ReviewPacket.job_score_id == JobScore.id) & (ReviewPacket.packet_version == "v1"),
        )
        .outerjoin(OpenClawReview, OpenClawReview.review_packet_id == ReviewPacket.id)
        .where(JobScore.profile_id == pid)
        .where(JobScore.rank_model_id == rid)
        .where(JobScore.passed_hard_filters.is_(True))
        .where(JobScore.score_total >= profile.ranking.minimum_score)
        .where(OpenClawReview.id.is_(None))
        .order_by(JobScore.score_total.desc(), JobCluster.id.asc())
        .limit(limit)
    )
    rows: list[dict] = []
    for cluster, score, job, source, packet in session.execute(stmt):
        rows.append(
            {
                "cluster_id": cluster.id,
                "job_id": job.id,
                "job_score_id": score.id,
                "title": job.title,
                "company_name": job.company_name,
                "score_total": round(float(score.score_total), 2),
                "source_name": source.name,
                "review_packet_status": packet.status if packet is not None else None,
                "review_packet_exported_at": packet.exported_at.isoformat() if packet is not None and packet.exported_at else None,
            }
        )
    return rows


def count_application_draft_pending(session: Session, profile: ProfileConfig, state_root: Path, *, preview_limit: int = 500) -> int:
    return len(fetch_applications_queue_rows(session, profile, state_root, limit=preview_limit))


def fetch_applications_queue_rows(
    session: Session,
    profile: ProfileConfig,
    state_root: Path,
    *,
    limit: int,
) -> list[dict]:
    previews = fetch_job_previews(session, profile, all_scored=False, limit=limit, snippet_length=120)
    rows: list[dict] = []
    for p in previews:
        root = state_root / p.job_id
        packet = root / "application_packet.json"
        cover = root / "cover_letter.draft.md"
        oc_req = root / "openclaw" / "cover_letter.request.json"
        oc_res = root / "openclaw" / "cover_letter.result.json"
        missing = root / "missing_inputs.yaml"
        reason: str | None = None
        if not packet.exists():
            reason = "missing_application_packet"
        elif not cover.exists():
            reason = "missing_cover_letter_draft"
        elif missing.exists():
            raw = missing.read_text(encoding="utf-8").strip()
            if raw and raw not in ("[]", "null", "{}"):
                reason = "has_missing_inputs"
        elif oc_req.exists() and not oc_res.exists():
            reason = "pending_openclaw_cover_letter"
        if reason:
            rows.append(
                {
                    "job_id": p.job_id,
                    "cluster_id": p.cluster_id,
                    "title": p.title,
                    "company_name": p.company_name,
                    "score_total": p.score_total,
                    "reason": reason,
                }
            )
    return rows
