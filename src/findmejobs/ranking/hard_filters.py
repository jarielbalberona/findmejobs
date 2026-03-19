from __future__ import annotations

from datetime import timedelta

from findmejobs.config.models import ProfileConfig
from findmejobs.domain.job import CanonicalJob
from findmejobs.utils.text import normalize_company_name
from findmejobs.utils.time import ensure_utc, utcnow


def evaluate_hard_filters(job: CanonicalJob, profile: ProfileConfig, feedback_types: list[str] | None = None) -> list[str]:
    reasons: list[str] = []
    feedback_types = feedback_types or []
    blocked_companies = {normalize_company_name(value) for value in profile.ranking.blocked_companies}
    if normalize_company_name(job.company_name) in blocked_companies:
        reasons.append("blocked_company")
    if "blocked_company" in feedback_types:
        reasons.append("feedback_blocked_company")

    title_lower = job.title.casefold()
    for keyword in profile.ranking.blocked_title_keywords:
        if keyword.casefold() in title_lower:
            reasons.append("blocked_title_keyword")
            break
    if "blocked_title" in feedback_types:
        reasons.append("feedback_blocked_title")

    if profile.ranking.require_remote and job.location_type != "remote":
        reasons.append("not_remote")

    allowed_countries = profile.allowed_countries or profile.ranking.allowed_countries
    if allowed_countries and job.country_code and job.country_code not in allowed_countries:
        reasons.append("country_not_allowed")
    allowed_companies = {normalize_company_name(value) for value in profile.ranking.allowed_companies}
    if allowed_companies and normalize_company_name(job.company_name) not in allowed_companies:
        reasons.append("company_not_allowed")

    if profile.ranking.minimum_salary is not None and job.salary_max is not None:
        if job.salary_max < profile.ranking.minimum_salary:
            reasons.append("salary_below_minimum")

    recency_anchor = ensure_utc(job.posted_at or job.first_seen_at)
    if recency_anchor < utcnow() - timedelta(days=profile.ranking.stale_days):
        reasons.append("stale_posting")
    if "ignore" in feedback_types or "applied" in feedback_types:
        reasons.append("feedback_suppressed")

    return reasons
