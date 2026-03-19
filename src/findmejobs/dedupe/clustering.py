from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.db.models import JobCluster, JobClusterMember, NormalizedJob, Source, SourceJob
from findmejobs.dedupe.matcher import find_cluster_for_job
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.time import ensure_utc, utcnow


def assign_job_cluster(session: Session, job: NormalizedJob, id_factory) -> tuple[JobCluster, bool]:
    session.flush()
    existing = session.scalar(
        select(JobCluster)
        .join(JobClusterMember, JobClusterMember.cluster_id == JobCluster.id)
        .where(JobClusterMember.normalized_job_id == job.id)
    )
    if existing:
        return existing, True

    match = find_cluster_for_job(session, job)
    now = utcnow()
    if match.cluster is None:
        cluster = JobCluster(
            id=id_factory(),
            cluster_key=sha256_hexdigest(f"{job.company_name}|{job.title}|{job.location_text}|{job.id}")[:24],
            representative_job_id=job.id,
            created_at=now,
            updated_at=now,
        )
        session.add(cluster)
        session.flush()
        session.add(
            JobClusterMember(
                id=id_factory(),
                cluster_id=cluster.id,
                normalized_job_id=job.id,
                match_rule="new_cluster",
                match_score=1.0,
                is_representative=True,
            )
        )
        session.flush()
        return cluster, False

    cluster = match.cluster
    cluster.updated_at = now
    session.add(
        JobClusterMember(
            id=id_factory(),
            cluster_id=cluster.id,
            normalized_job_id=job.id,
            match_rule=match.rule,
            match_score=match.score,
            is_representative=False,
        )
    )
    cluster.representative_job_id = choose_representative_job(session, cluster, job)
    session.flush()
    return cluster, True


def choose_representative_job(session: Session, cluster: JobCluster, candidate: NormalizedJob) -> str:
    members = session.scalars(
        select(NormalizedJob)
        .join(JobClusterMember, JobClusterMember.normalized_job_id == NormalizedJob.id)
        .where(JobClusterMember.cluster_id == cluster.id)
    ).all()
    members.append(candidate)
    members = list({member.id: member for member in members}.values())
    best = sorted(
        members,
        key=lambda item: _representative_sort_key(session, item),
        reverse=True,
    )[0]
    return best.id


def _representative_sort_key(session: Session, job: NormalizedJob) -> tuple[float, int, int, int, float]:
    source = session.execute(
        select(Source)
        .join(SourceJob, SourceJob.source_id == Source.id)
        .where(SourceJob.id == job.source_job_id)
        .limit(1)
    ).scalar_one_or_none()
    trust_weight = source.trust_weight if source is not None else 1.0
    priority = source.priority if source is not None else 0
    # Use a numeric key so SQLite-loaded naive datetimes never get compared to
    # in-memory timezone-aware values (TypeError in sorted()).
    last_seen_ts = ensure_utc(job.last_seen_at).timestamp()
    return (
        trust_weight,
        priority,
        1 if job.salary_max or job.salary_min else 0,
        len(job.description_text or ""),
        last_seen_ts,
    )
