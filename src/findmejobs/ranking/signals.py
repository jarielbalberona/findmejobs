from __future__ import annotations

from datetime import timedelta

from rapidfuzz import fuzz

from findmejobs.config.models import ProfileConfig
from findmejobs.domain.job import CanonicalJob
from findmejobs.utils.time import ensure_utc


def score_title_alignment(job: CanonicalJob, profile: ProfileConfig) -> float:
    if not profile.target_titles:
        return 0.0
    best = max(fuzz.token_set_ratio(job.title, title) for title in profile.target_titles)
    return best / 100.0


def score_title_family(job: CanonicalJob, profile: ProfileConfig) -> float:
    families = profile.ranking.title_families
    if not families:
        return 0.0
    comparisons: list[int] = []
    for title in profile.target_titles:
        aliases = families.get(title, [])
        for alias in aliases:
            comparisons.append(fuzz.token_set_ratio(job.title, alias))
    if not comparisons:
        return 0.0
    return max(comparisons) / 100.0


def score_skill_alignment(job: CanonicalJob, skills: list[str]) -> float:
    if not skills:
        return 0.0
    haystack = f"{job.title} {job.description_text} {' '.join(job.tags)}".casefold()
    matched = sum(1 for skill in skills if skill.casefold() in haystack)
    return matched / len(skills)


def score_location_fit(job: CanonicalJob, profile: ProfileConfig) -> float:
    if not profile.preferred_locations:
        return 1.0 if job.location_type in {"remote", "hybrid"} else 0.5
    haystack = job.location_text.casefold()
    if any(location.casefold() in haystack for location in profile.preferred_locations):
        return 1.0
    if job.location_type == "remote":
        return 0.75
    return 0.0


def score_remote_fit(job: CanonicalJob, profile: ProfileConfig) -> float:
    if profile.ranking.require_remote:
        return 1.0 if job.location_type == "remote" else 0.0
    if profile.ranking.remote_first:
        return {"remote": 1.0, "hybrid": 0.6, "onsite": 0.0}.get(job.location_type, 0.1)
    return {"remote": 1.0, "hybrid": 0.6, "onsite": 0.25}.get(job.location_type, 0.1)


def score_recency(job: CanonicalJob, profile: ProfileConfig) -> float:
    anchor = ensure_utc(job.posted_at or job.first_seen_at)
    last_seen = ensure_utc(job.last_seen_at)
    age_days = max((last_seen - anchor).days, 0)
    stale_days = max(profile.ranking.stale_days, 1)
    return max(0.0, 1.0 - ((age_days / stale_days) ** 1.5))


def score_company_preference(job: CanonicalJob, profile: ProfileConfig) -> float:
    preferred = {company.casefold() for company in profile.ranking.preferred_companies}
    if not preferred:
        return 0.0
    return 1.0 if job.company_name.casefold() in preferred else 0.0


def score_timezone_fit(job: CanonicalJob, profile: ProfileConfig) -> float:
    preferred_timezones = profile.ranking.preferred_timezones
    if not preferred_timezones:
        return 0.0
    haystack = f"{job.location_text} {job.description_text}".casefold()
    if any(tz.casefold() in haystack for tz in preferred_timezones):
        return 1.0
    if job.country_code and any(_timezone_country_hint(tz) == job.country_code for tz in preferred_timezones):
        return 0.75
    return 0.0


def score_source_trust(job: CanonicalJob) -> float:
    return min(max(job.source_trust_weight, 0.0), 2.0) / 2.0


def score_feedback_signal(feedback_types: list[str] | None) -> float:
    feedback_types = feedback_types or []
    if "relevant" in feedback_types:
        return 1.0
    if "irrelevant" in feedback_types:
        return -1.0
    return 0.0


def _timezone_country_hint(value: str) -> str | None:
    normalized = value.casefold()
    if "phil" in normalized or "manila" in normalized or "gmt+8" in normalized:
        return "PH"
    if "est" in normalized or "pst" in normalized or "us" in normalized:
        return "US"
    if "utc" in normalized or "gmt" in normalized:
        return None
    return None
