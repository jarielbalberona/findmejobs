from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.config.models import ProfileConfig, SourceConfig
from findmejobs.db.models import (
    DeliveryEvent,
    Digest,
    DigestItem,
    JobFeedback,
    JobScore,
    NormalizedJob,
    PipelineRun,
    Profile,
    RankModel,
    RawDocument,
    ReviewPacket,
    Source,
    SourceFetchRun,
    SourceJob,
)
from findmejobs.domain.job import CanonicalJob
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.time import utcnow


def upsert_source(session: Session, config: SourceConfig, id_factory) -> Source:
    source = session.scalar(select(Source).where(Source.name == config.name))
    now = utcnow()
    if source is None:
        source = Source(
            id=id_factory(),
            name=config.name,
            kind=config.kind,
            enabled=config.enabled,
            priority=config.priority,
            trust_weight=config.trust_weight,
            fetch_cap=config.fetch_cap,
            config_json=config.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        session.add(source)
        session.flush()
        return source
    source.kind = config.kind
    source.enabled = config.enabled
    source.priority = config.priority
    source.trust_weight = config.trust_weight
    source.fetch_cap = config.fetch_cap
    source.config_json = config.model_dump(mode="json")
    source.updated_at = now
    return source


def create_fetch_run(session: Session, source_id: str, id_factory) -> SourceFetchRun:
    run = SourceFetchRun(
        id=id_factory(),
        source_id=source_id,
        started_at=utcnow(),
        status="running",
        attempt_count=1,
        item_count=0,
    )
    session.add(run)
    session.flush()
    return run


def finish_fetch_run(
    fetch_run: SourceFetchRun,
    *,
    status: str,
    http_status: int | None,
    item_count: int,
    raw_seen_count: int = 0,
    seen_count: int = 0,
    skipped_count: int = 0,
    inserted_count: int = 0,
    updated_count: int = 0,
    failed_count: int = 0,
    parse_error_count: int = 0,
    dedupe_merge_count: int = 0,
    normalized_valid_count: int = 0,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    fetch_run.finished_at = utcnow()
    fetch_run.status = status
    fetch_run.http_status = http_status
    fetch_run.item_count = item_count
    fetch_run.raw_seen_count = raw_seen_count
    fetch_run.seen_count = seen_count
    fetch_run.skipped_count = skipped_count
    fetch_run.inserted_count = inserted_count
    fetch_run.updated_count = updated_count
    fetch_run.failed_count = failed_count
    fetch_run.parse_error_count = parse_error_count
    fetch_run.dedupe_merge_count = dedupe_merge_count
    fetch_run.normalized_valid_count = normalized_valid_count
    fetch_run.error_code = error_code
    fetch_run.error_message = error_message


def get_or_create_raw_document(session: Session, source_id: str, fetch_run_id: str, artifact, id_factory) -> RawDocument:
    document = session.scalar(
        select(RawDocument).where(RawDocument.source_id == source_id, RawDocument.sha256 == artifact.sha256)
    )
    if document is not None:
        return document
    document = RawDocument(
        id=id_factory(),
        source_id=source_id,
        fetch_run_id=fetch_run_id,
        url=artifact.fetched_url,
        canonical_url=artifact.final_url,
        content_type=artifact.content_type,
        http_status=artifact.status_code,
        sha256=artifact.sha256,
        storage_path=artifact.storage_path,
        fetched_at=artifact.fetched_at,
    )
    session.add(document)
    session.flush()
    return document


def upsert_source_job(
    session: Session,
    source_id: str,
    raw_document_id: str,
    fetch_run_id: str,
    record,
    id_factory,
) -> tuple[SourceJob, bool]:
    source_job = session.scalar(
        select(SourceJob).where(SourceJob.source_id == source_id, SourceJob.source_job_key == record.source_job_key)
    )
    now = utcnow()
    if source_job is None:
        source_job = SourceJob(
            id=id_factory(),
            source_id=source_id,
            raw_document_id=raw_document_id,
            fetch_run_id=fetch_run_id,
            source_job_key=record.source_job_key,
            source_url=record.source_url,
            apply_url=record.apply_url,
            payload_json=record.raw_payload,
            seen_at=now,
        )
        session.add(source_job)
        session.flush()
        return source_job, True
    source_job.raw_document_id = raw_document_id
    source_job.fetch_run_id = fetch_run_id
    source_job.source_url = record.source_url
    source_job.apply_url = record.apply_url
    source_job.payload_json = record.raw_payload
    source_job.seen_at = now
    source_job.closed_at = None
    return source_job, False


def upsert_normalized_job(session: Session, job: CanonicalJob, id_factory) -> tuple[NormalizedJob, bool]:
    normalized = session.scalar(select(NormalizedJob).where(NormalizedJob.source_job_id == job.source_job_id))
    status = "valid" if not job.normalization_errors else "invalid"
    if normalized is None:
        normalized = NormalizedJob(
            id=id_factory(),
            source_job_id=job.source_job_id,
            canonical_url=job.canonical_url,
            company_name=job.company_name,
            title=job.title,
            location_text=job.location_text,
            location_type=job.location_type,
            country_code=job.country_code,
            city=job.city,
            region=job.region,
            seniority=job.seniority,
            employment_type=job.employment_type,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            description_text=job.description_text,
            description_sha256=sha256_hexdigest(job.description_text),
            tags_json=job.tags,
            posted_at=job.posted_at,
            first_seen_at=job.first_seen_at,
            last_seen_at=job.last_seen_at,
            normalization_status=status,
            normalization_errors_json=job.normalization_errors,
        )
        session.add(normalized)
        session.flush()
        return normalized, True

    normalized.canonical_url = job.canonical_url
    normalized.company_name = job.company_name
    normalized.title = job.title
    normalized.location_text = job.location_text
    normalized.location_type = job.location_type
    normalized.country_code = job.country_code
    normalized.city = job.city
    normalized.region = job.region
    normalized.seniority = job.seniority
    normalized.employment_type = job.employment_type
    normalized.salary_min = job.salary_min
    normalized.salary_max = job.salary_max
    normalized.salary_currency = job.salary_currency
    normalized.salary_period = job.salary_period
    normalized.description_text = job.description_text
    normalized.description_sha256 = sha256_hexdigest(job.description_text)
    normalized.tags_json = job.tags
    normalized.posted_at = job.posted_at
    normalized.last_seen_at = job.last_seen_at
    normalized.normalization_status = status
    normalized.normalization_errors_json = job.normalization_errors
    return normalized, False


def upsert_profile(session: Session, profile_config: ProfileConfig, id_factory) -> Profile:
    profile = session.scalar(select(Profile).where(Profile.version == profile_config.version))
    now = utcnow()
    if profile is None:
        profile = Profile(
            id=id_factory(),
            version=profile_config.version,
            profile_json=profile_config.model_dump(mode="json"),
            created_at=now,
            is_active=True,
        )
        session.add(profile)
        session.flush()
        return profile
    profile.profile_json = profile_config.model_dump(mode="json")
    profile.is_active = True
    return profile


def upsert_rank_model(session: Session, profile_config: ProfileConfig, id_factory) -> RankModel:
    model = session.scalar(select(RankModel).where(RankModel.version == profile_config.rank_model_version))
    now = utcnow()
    config_json = profile_config.ranking.model_dump(mode="json")
    if model is None:
        model = RankModel(
            id=id_factory(),
            version=profile_config.rank_model_version,
            config_json=config_json,
            created_at=now,
            is_active=True,
        )
        session.add(model)
        session.flush()
        return model
    model.config_json = config_json
    model.is_active = True
    return model


def upsert_job_score(session: Session, cluster_id: str, profile_id: str, rank_model_id: str, breakdown, id_factory) -> JobScore:
    score = session.scalar(
        select(JobScore).where(
            JobScore.cluster_id == cluster_id,
            JobScore.profile_id == profile_id,
            JobScore.rank_model_id == rank_model_id,
        )
    )
    now = utcnow()
    if score is None:
        score = JobScore(
            id=id_factory(),
            cluster_id=cluster_id,
            profile_id=profile_id,
            rank_model_id=rank_model_id,
            passed_hard_filters=not breakdown.hard_filter_reasons,
            hard_filter_reasons_json=breakdown.hard_filter_reasons,
            score_total=breakdown.total,
            score_breakdown_json=breakdown.components,
            scored_at=now,
        )
        session.add(score)
        session.flush()
        return score
    score.passed_hard_filters = not breakdown.hard_filter_reasons
    score.hard_filter_reasons_json = breakdown.hard_filter_reasons
    score.score_total = breakdown.total
    score.score_breakdown_json = breakdown.components
    score.scored_at = now
    return score


def upsert_review_packet(session: Session, cluster_id: str, job_score_id: str, packet, id_factory) -> ReviewPacket:
    record = session.scalar(
        select(ReviewPacket).where(
            ReviewPacket.cluster_id == cluster_id,
            ReviewPacket.job_score_id == job_score_id,
            ReviewPacket.packet_version == packet.packet_version,
        )
    )
    now = utcnow()
    payload = packet.model_dump(mode="json")
    digest = sha256_hexdigest(packet.model_dump_json())
    if record is None:
        record = ReviewPacket(
            id=packet.packet_id,
            cluster_id=cluster_id,
            job_score_id=job_score_id,
            packet_version=packet.packet_version,
            packet_json=payload,
            packet_sha256=digest,
            status="built",
            built_at=now,
        )
        session.add(record)
        session.flush()
        return record
    record.packet_json = payload
    record.packet_sha256 = digest
    record.status = "built"
    record.built_at = now
    return record


def create_pipeline_run(session: Session, command: str, id_factory) -> PipelineRun:
    run = PipelineRun(
        id=id_factory(),
        command=command,
        started_at=utcnow(),
        status="running",
        stats_json={},
    )
    session.add(run)
    session.flush()
    return run


def finish_pipeline_run(run: PipelineRun, status: str, stats: dict | None = None, error_message: str | None = None) -> None:
    run.finished_at = utcnow()
    run.status = status
    run.stats_json = stats or {}
    run.error_message = error_message


def create_job_feedback(
    session: Session,
    *,
    id_factory,
    feedback_type: str,
    cluster_id: str | None = None,
    company_name: str | None = None,
    title_keyword: str | None = None,
    notes: str | None = None,
) -> JobFeedback:
    record = JobFeedback(
        id=id_factory(),
        cluster_id=cluster_id,
        feedback_type=feedback_type,
        company_name=company_name,
        title_keyword=title_keyword,
        notes=notes,
        created_at=utcnow(),
    )
    session.add(record)
    session.flush()
    return record


def create_digest(session: Session, *, id_factory, channel: str, digest_date: str, window_start, window_end, subject: str, body_text: str, resend_of_digest_id: str | None = None) -> Digest:
    digest = Digest(
        id=id_factory(),
        channel=channel,
        digest_date=digest_date,
        window_start=window_start,
        window_end=window_end,
        status="built",
        subject=subject,
        body_text=body_text,
        resend_of_digest_id=resend_of_digest_id,
    )
    session.add(digest)
    session.flush()
    return digest


def add_digest_item(session: Session, *, id_factory, digest_id: str, cluster_id: str, review_id: str, job_score_id: str, position: int, item_json: dict, score_at_send: float) -> DigestItem:
    item = DigestItem(
        id=id_factory(),
        digest_id=digest_id,
        cluster_id=cluster_id,
        review_id=review_id,
        job_score_id=job_score_id,
        position=position,
        item_json=item_json,
        score_at_send=score_at_send,
    )
    session.add(item)
    session.flush()
    return item


def create_delivery_event(
    session: Session,
    *,
    id_factory,
    channel: str,
    status: str,
    attempt: int,
    digest_id: str | None = None,
    provider_message_id: str | None = None,
    error_message: str | None = None,
    metadata_json: dict | None = None,
) -> DeliveryEvent:
    event = DeliveryEvent(
        id=id_factory(),
        digest_id=digest_id,
        channel=channel,
        status=status,
        attempt=attempt,
        provider_message_id=provider_message_id,
        error_message=error_message,
        metadata_json=metadata_json or {},
        created_at=utcnow(),
    )
    session.add(event)
    session.flush()
    return event
