from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.config.models import AppConfig, ProfileConfig
from findmejobs.db.models import JobCluster, JobScore, NormalizedJob, OpenClawReview, ReviewPacket
from findmejobs.db.repositories import upsert_review_packet
from findmejobs.domain.job import CanonicalJob
from findmejobs.review.client import FilesystemOpenClawClient
from findmejobs.review.importer import import_review_result
from findmejobs.review.packets import build_review_packet
from findmejobs.utils.hashing import sha256_hexdigest


def export_review_packets(session: Session, app_config: AppConfig, profile: ProfileConfig, id_factory) -> int:
    client = FilesystemOpenClawClient(app_config.storage.review_outbox_dir, app_config.storage.review_inbox_dir)
    stmt = (
        select(JobCluster, JobScore, NormalizedJob)
        .join(JobScore, JobScore.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobCluster.representative_job_id)
        .outerjoin(ReviewPacket, ReviewPacket.job_score_id == JobScore.id)
        .outerjoin(OpenClawReview, OpenClawReview.review_packet_id == ReviewPacket.id)
        .where(JobScore.passed_hard_filters.is_(True))
        .where(JobScore.score_total >= profile.ranking.minimum_score)
        .where(OpenClawReview.id.is_(None))
    )
    exported = 0
    for cluster, score, job_row in session.execute(stmt):
        job = canonical_job_from_row(job_row)
        packet_id = sha256_hexdigest(f"{cluster.id}|{score.id}|v1")[:26]
        packet = build_review_packet(packet_id, cluster.id, job, score.score_total, score.score_breakdown_json)
        record = upsert_review_packet(session, cluster.id, score.id, packet, id_factory)
        client.export_packet(packet)
        record.status = "exported"
        record.exported_at = record.built_at
        exported += 1
    session.commit()
    return exported


def import_review_packets(session: Session, app_config: AppConfig, id_factory) -> int:
    client = FilesystemOpenClawClient(app_config.storage.review_outbox_dir, app_config.storage.review_inbox_dir)
    imported = 0
    for result in client.load_results():
        if import_review_result(session, result, id_factory):
            imported += 1
    session.commit()
    return imported


def canonical_job_from_row(row: NormalizedJob) -> CanonicalJob:
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
