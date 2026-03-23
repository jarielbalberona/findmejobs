from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.db.models import JobCluster, JobClusterMember, NormalizedJob, SourceJob
from findmejobs.utils.text import normalize_company_name, normalize_location, normalize_title


@dataclass(slots=True)
class MatchResult:
    cluster: JobCluster | None
    rule: str
    score: float


def find_cluster_for_job(session: Session, job: NormalizedJob) -> MatchResult:
    if job.canonical_url:
        cluster = _cluster_by_canonical_url(session, job)
        if cluster:
            return MatchResult(cluster, "canonical_url", 1.0)

    cluster = _cluster_by_source_job_key(session, job)
    if cluster:
        return MatchResult(cluster, "source_job_key", 0.95)

    cluster = _cluster_by_exact_identity(session, job)
    if cluster:
        return MatchResult(cluster, "normalized_identity", 0.9)

    cluster = _cluster_by_description_signature(session, job)
    if cluster:
        return MatchResult(cluster, "description_signature", 0.85)

    return MatchResult(None, "new_cluster", 0.0)


def _cluster_by_canonical_url(session: Session, job: NormalizedJob) -> JobCluster | None:
    stmt = (
        select(JobCluster)
        .join(JobClusterMember, JobClusterMember.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobClusterMember.normalized_job_id)
        .where(NormalizedJob.canonical_url == job.canonical_url)
        .where(NormalizedJob.id != job.id)
        .limit(1)
    )
    return session.scalar(stmt)


def _cluster_by_source_job_key(session: Session, job: NormalizedJob) -> JobCluster | None:
    current_source_job = session.scalar(select(SourceJob).where(SourceJob.id == job.source_job_id))
    if current_source_job is None:
        return None
    stmt = (
        select(JobCluster)
        .join(JobClusterMember, JobClusterMember.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobClusterMember.normalized_job_id)
        .join(SourceJob, SourceJob.id == NormalizedJob.source_job_id)
        .where(NormalizedJob.id != job.id)
        .where(SourceJob.source_id == current_source_job.source_id)
        .where(SourceJob.source_job_key == current_source_job.source_job_key)
        .limit(1)
    )
    return session.scalar(stmt)


def _cluster_by_exact_identity(session: Session, job: NormalizedJob) -> JobCluster | None:
    normalized_company = normalize_company_name(job.company_name)
    normalized_title = normalize_title(job.title)
    normalized_location = normalize_location(job.location_text)
    stmt = (
        select(JobCluster, NormalizedJob)
        .join(JobClusterMember, JobClusterMember.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobClusterMember.normalized_job_id)
        .where(NormalizedJob.id != job.id)
    )
    if job.location_type != "unknown":
        stmt = stmt.where(NormalizedJob.location_type == job.location_type)
    if job.country_code:
        stmt = stmt.where((NormalizedJob.country_code == job.country_code) | (NormalizedJob.country_code.is_(None)))
    for cluster, candidate in session.execute(stmt):
        if normalize_company_name(candidate.company_name) != normalized_company:
            continue
        if normalize_title(candidate.title) != normalized_title:
            continue
        if normalize_location(candidate.location_text) != normalized_location:
            continue
        return cluster
    return None


def _cluster_by_description_signature(session: Session, job: NormalizedJob) -> JobCluster | None:
    if not job.description_sha256:
        return None
    normalized_company = normalize_company_name(job.company_name)
    normalized_title = normalize_title(job.title)
    stmt = (
        select(JobCluster, NormalizedJob)
        .join(JobClusterMember, JobClusterMember.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobClusterMember.normalized_job_id)
        .where(NormalizedJob.id != job.id)
        .where(NormalizedJob.description_sha256 == job.description_sha256)
    )
    for cluster, candidate in session.execute(stmt):
        if normalize_company_name(candidate.company_name) != normalized_company:
            continue
        if normalize_title(candidate.title) != normalized_title:
            continue
        if not _location_types_compatible(candidate.location_type, job.location_type):
            continue
        if not _country_codes_compatible(candidate.country_code, job.country_code):
            continue
        return cluster
    return None


def _location_types_compatible(left: str, right: str) -> bool:
    return left == right or left == "unknown" or right == "unknown"


def _country_codes_compatible(left: str | None, right: str | None) -> bool:
    return left == right or left is None or right is None
