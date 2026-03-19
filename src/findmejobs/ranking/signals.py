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
    return {"remote": 1.0, "hybrid": 0.6, "onsite": 0.25}.get(job.location_type, 0.1)


def score_recency(job: CanonicalJob, profile: ProfileConfig) -> float:
    anchor = ensure_utc(job.posted_at or job.first_seen_at)
    last_seen = ensure_utc(job.last_seen_at)
    age_days = max((last_seen - anchor).days, 0)
    stale_days = max(profile.ranking.stale_days, 1)
    return max(0.0, 1.0 - (age_days / stale_days))
