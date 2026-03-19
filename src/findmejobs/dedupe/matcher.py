from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.db.models import JobCluster, JobClusterMember, NormalizedJob, SourceJob


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
        .where(SourceJob.source_job_key == current_source_job.source_job_key)
        .limit(1)
    )
    return session.scalar(stmt)


def _cluster_by_exact_identity(session: Session, job: NormalizedJob) -> JobCluster | None:
    stmt = (
        select(JobCluster)
        .join(JobClusterMember, JobClusterMember.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobClusterMember.normalized_job_id)
        .where(NormalizedJob.id != job.id)
        .where(NormalizedJob.company_name == job.company_name)
        .where(NormalizedJob.title == job.title)
        .where(NormalizedJob.location_text == job.location_text)
        .limit(1)
    )
    return session.scalar(stmt)
