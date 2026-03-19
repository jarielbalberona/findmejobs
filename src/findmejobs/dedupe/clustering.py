from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from findmejobs.db.models import JobCluster, JobClusterMember, NormalizedJob
from findmejobs.dedupe.matcher import find_cluster_for_job
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.time import utcnow


def assign_job_cluster(session: Session, job: NormalizedJob, id_factory) -> JobCluster:
    existing = session.scalar(
        select(JobCluster)
        .join(JobClusterMember, JobClusterMember.cluster_id == JobCluster.id)
        .where(JobClusterMember.normalized_job_id == job.id)
    )
    if existing:
        return existing

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
        return cluster

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
    return cluster


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
        key=lambda item: (
            1 if item.salary_max or item.salary_min else 0,
            len(item.description_text or ""),
            item.last_seen_at,
        ),
        reverse=True,
    )[0]
    return best.id
