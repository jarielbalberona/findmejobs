from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from findmejobs.db.models import JobCluster, JobFeedback, NormalizedJob
from findmejobs.db.repositories import create_job_feedback
from findmejobs.utils.text import normalize_company_name, normalize_title

ALLOWED_FEEDBACK_TYPES = {
    "relevant",
    "irrelevant",
    "applied",
    "ignore",
    "blocked_company",
    "blocked_title",
}


def record_feedback(
    session: Session,
    *,
    id_factory,
    feedback_type: str,
    cluster_id: str | None = None,
    company_name: str | None = None,
    title_keyword: str | None = None,
    notes: str | None = None,
) -> JobFeedback:
    if feedback_type not in ALLOWED_FEEDBACK_TYPES:
        raise ValueError(f"invalid_feedback_type:{feedback_type}")
    if cluster_id is not None and (company_name is None or title_keyword is None):
        cluster = session.get(JobCluster, cluster_id)
        if cluster is not None and cluster.representative_job_id is not None:
            job = session.get(NormalizedJob, cluster.representative_job_id)
            if job is not None:
                company_name = company_name or job.company_name
                title_keyword = title_keyword or job.title
    if feedback_type == "blocked_company" and not company_name:
        raise ValueError("blocked_company_requires_company_name_or_cluster")
    if feedback_type == "blocked_title" and not title_keyword:
        raise ValueError("blocked_title_requires_title_keyword_or_cluster")
    return create_job_feedback(
        session,
        id_factory=id_factory,
        feedback_type=feedback_type,
        cluster_id=cluster_id,
        company_name=company_name,
        title_keyword=title_keyword,
        notes=notes,
    )


def feedback_types_for_job(session: Session, *, cluster_id: str, company_name: str, title: str) -> list[str]:
    normalized_company = normalize_company_name(company_name)
    normalized_title_value = normalize_title(title)
    rows = session.scalars(
        select(JobFeedback).where(
            or_(
                JobFeedback.cluster_id == cluster_id,
                JobFeedback.feedback_type.in_(["blocked_company", "blocked_title"]),
            )
        )
    ).all()
    types: list[str] = []
    for row in rows:
        if row.cluster_id == cluster_id:
            types.append(row.feedback_type)
            continue
        if row.feedback_type == "blocked_company" and row.company_name and normalize_company_name(row.company_name) == normalized_company:
            types.append(row.feedback_type)
            continue
        if row.feedback_type == "blocked_title" and row.title_keyword and normalize_title(row.title_keyword) in normalized_title_value:
            types.append(row.feedback_type)
    return sorted(set(types))
