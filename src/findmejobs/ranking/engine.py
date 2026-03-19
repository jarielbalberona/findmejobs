from __future__ import annotations

from findmejobs.config.models import ProfileConfig
from findmejobs.domain.job import CanonicalJob
from findmejobs.domain.ranking import ScoreBreakdown
from findmejobs.ranking.hard_filters import evaluate_hard_filters
from findmejobs.ranking.signals import (
    score_company_preference,
    score_feedback_signal,
    score_location_fit,
    score_recency,
    score_remote_fit,
    score_skill_alignment,
    score_source_trust,
    score_timezone_fit,
    score_title_family,
    score_title_alignment,
)


def rank_job(job: CanonicalJob, profile: ProfileConfig) -> ScoreBreakdown:
    return rank_job_with_feedback(job, profile, feedback_types=[])


def rank_job_with_feedback(job: CanonicalJob, profile: ProfileConfig, *, feedback_types: list[str]) -> ScoreBreakdown:
    breakdown = ScoreBreakdown()
    breakdown.hard_filter_reasons = evaluate_hard_filters(job, profile, feedback_types=feedback_types)
    weights = profile.ranking.weights
    breakdown.components = {
        "title_alignment": round(score_title_alignment(job, profile) * weights.title_alignment, 2),
        "title_family": round(score_title_family(job, profile) * weights.title_family, 2),
        "must_have_skills": round(score_skill_alignment(job, profile.required_skills) * weights.must_have_skills, 2),
        "preferred_skills": round(score_skill_alignment(job, profile.preferred_skills) * weights.preferred_skills, 2),
        "location_fit": round(score_location_fit(job, profile) * weights.location_fit, 2),
        "remote_fit": round(score_remote_fit(job, profile) * weights.remote_fit, 2),
        "recency": round(score_recency(job, profile) * weights.recency, 2),
        "company_preference": round(score_company_preference(job, profile) * weights.company_preference, 2),
        "timezone_fit": round(score_timezone_fit(job, profile) * weights.timezone_fit, 2),
        "source_trust": round(score_source_trust(job) * weights.source_trust, 2),
        "feedback_signal": round(score_feedback_signal(feedback_types) * weights.feedback_signal, 2),
    }
    return breakdown
