from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.db.models import OpenClawReview, ReviewPacket
from findmejobs.domain.review import ReviewResultModel
from findmejobs.utils.time import utcnow


def import_review_result(session: Session, result: ReviewResultModel, id_factory) -> bool:
    packet = session.scalar(select(ReviewPacket).where(ReviewPacket.id == result.packet_id))
    if packet is None:
        raise ValueError(f"unknown packet id {result.packet_id}")

    existing = session.scalar(select(OpenClawReview).where(OpenClawReview.review_packet_id == packet.id))
    if existing:
        return False

    session.add(
        OpenClawReview(
            id=id_factory(),
            review_packet_id=packet.id,
            provider_review_id=result.provider_review_id,
            decision=result.decision,
            confidence_label=result.confidence_label,
            reasons_json=result.reasons,
            draft_summary=result.draft_summary,
            draft_actions_json=result.draft_actions,
            raw_response_json=result.raw_response,
            reviewed_at=result.reviewed_at,
            imported_at=utcnow(),
        )
    )
    packet.status = "reviewed"
    return True
