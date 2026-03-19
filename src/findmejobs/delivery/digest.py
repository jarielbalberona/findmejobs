from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.config.models import AppConfig, ProfileConfig
from findmejobs.db.models import (
    DeliveryEvent,
    Digest,
    DigestItem,
    JobCluster,
    JobFeedback,
    RankModel,
    JobScore,
    NormalizedJob,
    OpenClawReview,
    ReviewPacket,
    Source,
    SourceJob,
)
from findmejobs.db.repositories import add_digest_item, create_delivery_event, create_digest
from findmejobs.delivery.email import EmailSendResult, SMTPEmailSender
from findmejobs.utils.time import ensure_utc, utcnow


@dataclass(slots=True)
class DigestCandidate:
    cluster_id: str
    review_id: str
    job_score_id: str
    title: str
    company: str
    location: str
    source: str
    score: float
    why: str
    link: str | None


def build_digest_candidates(session: Session, profile: ProfileConfig, *, limit: int) -> list[DigestCandidate]:
    stmt = (
        select(JobCluster, JobScore, NormalizedJob, OpenClawReview, Source)
        .join(JobScore, JobScore.cluster_id == JobCluster.id)
        .join(RankModel, RankModel.id == JobScore.rank_model_id)
        .join(NormalizedJob, NormalizedJob.id == JobCluster.representative_job_id)
        .join(SourceJob, SourceJob.id == NormalizedJob.source_job_id)
        .join(Source, Source.id == SourceJob.source_id)
        .join(ReviewPacket, ReviewPacket.job_score_id == JobScore.id)
        .join(OpenClawReview, OpenClawReview.review_packet_id == ReviewPacket.id)
        .where(JobScore.passed_hard_filters.is_(True))
        .where(JobScore.score_total >= profile.ranking.minimum_score)
        .where(RankModel.version == profile.rank_model_version)
        .where(OpenClawReview.decision.in_(["keep", "strong_keep"]))
        .order_by(JobScore.score_total.desc(), JobScore.scored_at.desc(), JobCluster.id.asc())
    )
    candidates: list[DigestCandidate] = []
    for cluster, score, job, review, source in session.execute(stmt):
        if _should_suppress_for_feedback(session, cluster.id):
            continue
        if _already_sent_recently(session, cluster.id):
            continue
        candidates.append(
            DigestCandidate(
                cluster_id=cluster.id,
                review_id=review.id,
                job_score_id=score.id,
                title=job.title,
                company=job.company_name,
                location=job.location_text,
                source=source.name,
                score=score.score_total,
                why=_why_it_matched(score.score_breakdown_json),
                link=job.canonical_url,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def send_digest(
    session: Session,
    app_config: AppConfig,
    profile: ProfileConfig,
    *,
    id_factory,
    sender: SMTPEmailSender | None = None,
    digest_date: str | None = None,
    resend_of_digest_id: str | None = None,
    dry_run: bool = False,
) -> Digest:
    digest_date = digest_date or utcnow().strftime("%Y-%m-%d")
    existing = session.scalar(
        select(Digest).where(Digest.channel == app_config.delivery.channel, Digest.digest_date == digest_date, Digest.status == "sent")
    )
    if existing is not None and resend_of_digest_id is None:
        return existing

    candidates = build_digest_candidates(session, profile, limit=app_config.delivery.digest_max_items)
    body_text = render_digest_body(candidates)
    subject = f"findmejobs digest {digest_date}"
    digest = create_digest(
        session,
        id_factory=id_factory,
        channel=app_config.delivery.channel,
        digest_date=digest_date,
        window_start=utcnow() - timedelta(days=1),
        window_end=utcnow(),
        subject=subject,
        body_text=body_text,
        resend_of_digest_id=resend_of_digest_id,
    )
    for idx, candidate in enumerate(candidates, start=1):
        add_digest_item(
            session,
            id_factory=id_factory,
            digest_id=digest.id,
            cluster_id=candidate.cluster_id,
            review_id=candidate.review_id,
            job_score_id=candidate.job_score_id,
            position=idx,
            item_json={
                "title": candidate.title,
                "company": candidate.company,
                "location": candidate.location,
                "source": candidate.source,
                "score": candidate.score,
                "why_it_matched": candidate.why,
                "direct_link": candidate.link,
            },
            score_at_send=candidate.score,
        )
    session.flush()

    if dry_run:
        digest.status = "dry_run"
        return digest

    sender = sender or SMTPEmailSender(app_config.delivery.email)
    try:
        send_result = sender.send(subject=subject, body_text=body_text)
        attempts = getattr(sender, "last_attempt_count", 1)
        provider_message_id = send_result.provider_message_id if isinstance(send_result, EmailSendResult) else str(send_result)
        create_delivery_event(
            session,
            id_factory=id_factory,
            digest_id=digest.id,
            channel=app_config.delivery.channel,
            status="sent",
            attempt=attempts,
            provider_message_id=provider_message_id,
        )
        digest.status = "sent"
        digest.sent_at = utcnow()
    except Exception as exc:
        attempts = getattr(sender, "last_attempt_count", 1)
        create_delivery_event(
            session,
            id_factory=id_factory,
            digest_id=digest.id,
            channel=app_config.delivery.channel,
            status="failed",
            attempt=attempts,
            error_message=str(exc),
        )
        digest.status = "failed"
        raise
    return digest


def render_digest_body(candidates: list[DigestCandidate]) -> str:
    lines = ["findmejobs daily digest", ""]
    if not candidates:
        lines.append("No eligible reviewed jobs.")
        return "\n".join(lines) + "\n"
    for idx, candidate in enumerate(candidates, start=1):
        lines.extend(
            [
                f"{idx}. {candidate.title} at {candidate.company}",
                f"   Location: {candidate.location or 'Unknown'}",
                f"   Source: {candidate.source}",
                f"   Score: {candidate.score:.2f}",
                f"   Why: {candidate.why}",
                f"   Link: {candidate.link or 'N/A'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _why_it_matched(breakdown: dict) -> str:
    positive = [(key, value) for key, value in breakdown.items() if isinstance(value, (int, float)) and value > 0]
    top = sorted(positive, key=lambda item: item[1], reverse=True)[:2]
    if not top:
        return "Passed deterministic ranking"
    return ", ".join(key.replace("_", " ") for key, _ in top)


def _already_sent_recently(session: Session, cluster_id: str) -> bool:
    recent = session.scalar(
        select(DigestItem.id)
        .join(Digest, Digest.id == DigestItem.digest_id)
        .where(DigestItem.cluster_id == cluster_id)
        .where(Digest.status == "sent")
        .where(Digest.sent_at >= utcnow() - timedelta(days=7))
        .limit(1)
    )
    return recent is not None


def _should_suppress_for_feedback(session: Session, cluster_id: str) -> bool:
    feedback_types = session.scalars(select(JobFeedback.feedback_type).where(JobFeedback.cluster_id == cluster_id)).all()
    return any(item in {"ignore", "applied"} for item in feedback_types)
