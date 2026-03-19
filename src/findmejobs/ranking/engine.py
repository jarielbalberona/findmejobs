from __future__ import annotations

from findmejobs.config.models import ProfileConfig
from findmejobs.domain.job import CanonicalJob
from findmejobs.domain.ranking import ScoreBreakdown
from findmejobs.ranking.hard_filters import evaluate_hard_filters
from findmejobs.ranking.signals import (
    score_location_fit,
    score_recency,
    score_remote_fit,
    score_skill_alignment,
    score_title_alignment,
)


def rank_job(job: CanonicalJob, profile: ProfileConfig) -> ScoreBreakdown:
    breakdown = ScoreBreakdown()
    breakdown.hard_filter_reasons = evaluate_hard_filters(job, profile)
    weights = profile.ranking.weights
    breakdown.components = {
        "title_alignment": round(score_title_alignment(job, profile) * weights.title_alignment, 2),
        "must_have_skills": round(score_skill_alignment(job, profile.required_skills) * weights.must_have_skills, 2),
        "preferred_skills": round(score_skill_alignment(job, profile.preferred_skills) * weights.preferred_skills, 2),
        "location_fit": round(score_location_fit(job, profile) * weights.location_fit, 2),
        "remote_fit": round(score_remote_fit(job, profile) * weights.remote_fit, 2),
        "recency": round(score_recency(job, profile) * weights.recency, 2),
    }
    return breakdown
