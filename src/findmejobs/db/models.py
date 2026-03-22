from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from findmejobs.db.base import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trust_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    fetch_cap: Mapped[int | None] = mapped_column(Integer)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_successful_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceFetchRun(Base):
    __tablename__ = "source_fetch_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dedupe_merge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    normalized_valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class RawDocument(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("source_id", "sha256", name="uq_raw_document_source_sha"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    fetch_run_id: Mapped[str] = mapped_column(ForeignKey("source_fetch_runs.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text, index=True)
    content_type: Mapped[str | None] = mapped_column(String(255))
    http_status: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class SourceJob(Base):
    __tablename__ = "source_jobs"
    __table_args__ = (UniqueConstraint("source_id", "source_job_key", name="uq_source_jobs_source_key"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    raw_document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), nullable=False, index=True)
    fetch_run_id: Mapped[str] = mapped_column(ForeignKey("source_fetch_runs.id"), nullable=False, index=True)
    source_job_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    apply_url: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NormalizedJob(Base):
    __tablename__ = "normalized_jobs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    source_job_id: Mapped[str] = mapped_column(ForeignKey("source_jobs.id"), nullable=False, unique=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    location_text: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    location_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    city: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(255))
    seniority: Mapped[str | None] = mapped_column(String(32))
    employment_type: Mapped[str | None] = mapped_column(String(32))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    salary_period: Mapped[str | None] = mapped_column(String(32))
    description_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    normalization_status: Mapped[str] = mapped_column(String(32), nullable=False)
    normalization_errors_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class JobCluster(Base):
    __tablename__ = "job_clusters"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    cluster_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    representative_job_id: Mapped[str | None] = mapped_column(ForeignKey("normalized_jobs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class JobClusterMember(Base):
    __tablename__ = "job_cluster_members"
    __table_args__ = (UniqueConstraint("cluster_id", "normalized_job_id", name="uq_cluster_member"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("job_clusters.id"), nullable=False, index=True)
    normalized_job_id: Mapped[str] = mapped_column(ForeignKey("normalized_jobs.id"), nullable=False, index=True)
    match_rule: Mapped[str] = mapped_column(String(64), nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    is_representative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class RankModel(Base):
    __tablename__ = "rank_models"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class JobScore(Base):
    __tablename__ = "job_scores"
    __table_args__ = (
        UniqueConstraint("cluster_id", "profile_id", "rank_model_id", name="uq_job_score_cluster_profile_model"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("job_clusters.id"), nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    rank_model_id: Mapped[str] = mapped_column(ForeignKey("rank_models.id"), nullable=False, index=True)
    passed_hard_filters: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    hard_filter_reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    score_total: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    score_breakdown_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class JobFeedback(Base):
    __tablename__ = "job_feedback"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    cluster_id: Mapped[str | None] = mapped_column(ForeignKey("job_clusters.id"), index=True)
    feedback_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    company_name: Mapped[str | None] = mapped_column(String(255), index=True)
    title_keyword: Mapped[str | None] = mapped_column(String(255), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ApplicationSubmission(Base):
    __tablename__ = "application_submissions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("normalized_jobs.id"), nullable=False, index=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("job_clusters.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    external_ref: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ReviewPacket(Base):
    __tablename__ = "review_packets"
    __table_args__ = (
        UniqueConstraint("cluster_id", "job_score_id", "packet_version", name="uq_review_packet_cluster_score_version"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("job_clusters.id"), nullable=False, index=True)
    job_score_id: Mapped[str] = mapped_column(ForeignKey("job_scores.id"), nullable=False, index=True)
    packet_version: Mapped[str] = mapped_column(String(32), nullable=False)
    packet_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    packet_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpenClawReview(Base):
    __tablename__ = "openclaw_reviews"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    review_packet_id: Mapped[str] = mapped_column(ForeignKey("review_packets.id"), nullable=False, unique=True, index=True)
    provider_review_id: Mapped[str | None] = mapped_column(String(255))
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence_label: Mapped[str | None] = mapped_column(String(32))
    reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    draft_summary: Mapped[str | None] = mapped_column(Text)
    draft_actions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    command: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stats_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    digest_date: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    resend_of_digest_id: Mapped[str | None] = mapped_column(ForeignKey("digests.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class DigestItem(Base):
    __tablename__ = "digest_items"
    __table_args__ = (UniqueConstraint("digest_id", "cluster_id", name="uq_digest_cluster"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    digest_id: Mapped[str] = mapped_column(ForeignKey("digests.id"), nullable=False, index=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("job_clusters.id"), nullable=False, index=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("openclaw_reviews.id"), nullable=False, index=True)
    job_score_id: Mapped[str] = mapped_column(ForeignKey("job_scores.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    item_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    score_at_send: Mapped[float] = mapped_column(Float, nullable=False)


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    digest_id: Mapped[str | None] = mapped_column(ForeignKey("digests.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
