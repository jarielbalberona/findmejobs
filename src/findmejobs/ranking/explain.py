"""Operator-facing description of deterministic ranking: hard filters, config keys, score components."""

from __future__ import annotations

import json
from typing import Any

from findmejobs.config.models import ProfileConfig

# Reason codes must stay in sync with ranking/hard_filters.py and feedback integration.
HARD_FILTER_RULES: list[dict[str, Any]] = [
    {
        "reason": "blocked_company",
        "config": ["ranking.blocked_companies"],
        "note": "Normalized company match against blocklist.",
    },
    {
        "reason": "feedback_blocked_company",
        "config": ["(feedback)", "ranking.blocked_companies"],
        "note": "Operator feedback reinforced company block.",
    },
    {
        "reason": "blocked_title_keyword",
        "config": ["ranking.blocked_title_keywords"],
        "note": "Substring match on job title (case-insensitive).",
    },
    {
        "reason": "feedback_blocked_title",
        "config": ["(feedback)", "ranking.blocked_title_keywords"],
        "note": "Operator feedback reinforced title block.",
    },
    {
        "reason": "not_remote",
        "config": ["ranking.require_remote"],
        "note": "When true, only location_type=remote passes.",
    },
    {
        "reason": "country_not_allowed",
        "config": ["profile.allowed_countries (stored on RankingPolicy at load; see config/loader.py)"],
        "note": "Job country_code must be in allowed set when both job and list are present.",
    },
    {
        "reason": "company_not_allowed",
        "config": ["ranking.allowed_companies"],
        "note": "When non-empty, company must be in this allowlist.",
    },
    {
        "reason": "salary_below_minimum",
        "config": ["ranking.minimum_salary"],
        "note": "When both job.salary_max and minimum_salary are set, max must clear the floor.",
    },
    {
        "reason": "stale_posting",
        "config": ["ranking.stale_days"],
        "note": "Compared to posted_at or first_seen_at vs now.",
    },
    {
        "reason": "feedback_suppressed",
        "config": ["(feedback)", 'types "ignore" or "applied"'],
        "note": "Suppresses job based on recorded feedback.",
    },
]

# Keys in score_breakdown_json / matched_signals — must stay in sync with ranking/engine.py.
SCORE_COMPONENTS: list[dict[str, Any]] = [
    {"component": "title_alignment", "weight": "ranking.weights.title_alignment", "inputs": "profile.target_titles, job.title"},
    {"component": "title_family", "weight": "ranking.weights.title_family", "inputs": "ranking.title_families, job.title"},
    {"component": "must_have_skills", "weight": "ranking.weights.must_have_skills", "inputs": "profile.required_skills, job.tags/description"},
    {"component": "preferred_skills", "weight": "ranking.weights.preferred_skills", "inputs": "profile.preferred_skills, job.tags/description"},
    {"component": "location_fit", "weight": "ranking.weights.location_fit", "inputs": "profile.preferred_locations, job.location"},
    {"component": "remote_fit", "weight": "ranking.weights.remote_fit", "inputs": "ranking.require_remote, ranking.remote_first, job.location_type"},
    {"component": "recency", "weight": "ranking.weights.recency", "inputs": "job.posted_at/first_seen_at, ranking.stale_days"},
    {"component": "company_preference", "weight": "ranking.weights.company_preference", "inputs": "ranking.preferred_companies, job.company_name"},
    {"component": "timezone_fit", "weight": "ranking.weights.timezone_fit", "inputs": "ranking.preferred_timezones, job metadata"},
    {"component": "source_trust", "weight": "ranking.weights.source_trust", "inputs": "source.trust_weight at ingest"},
    {"component": "feedback_signal", "weight": "ranking.weights.feedback_signal", "inputs": "recorded feedback types for cluster/title/company"},
]


def build_ranking_explain_payload(
    profile: ProfileConfig,
    *,
    profile_path: str,
    ranking_path: str,
) -> dict[str, Any]:
    """Structured summary for CLI JSON or pretty-print."""
    return {
        "config_paths": {"profile": profile_path, "ranking": ranking_path},
        "rank_model_version": profile.rank_model_version,
        "review_eligibility": {
            "minimum_score": profile.ranking.minimum_score,
            "must_pass_hard_filters": True,
            "note": "Same gates as review export and digest candidates (plus review decisions for digest).",
        },
        "ranking_policy": profile.ranking.model_dump(mode="json"),
        "profile_fields_for_scoring": {
            "target_titles": profile.target_titles,
            "required_skills": profile.required_skills,
            "preferred_skills": profile.preferred_skills,
            "preferred_locations": profile.preferred_locations,
            "allowed_countries": profile.allowed_countries,
        },
        "hard_filter_rules": HARD_FILTER_RULES,
        "score_components": SCORE_COMPONENTS,
        "implementation": {
            "hard_filters": "findmejobs.ranking.hard_filters.evaluate_hard_filters",
            "score_pipeline": "findmejobs.ranking.engine.rank_job_with_feedback",
            "persistence": "job_scores.hard_filter_reasons_json, job_scores.score_breakdown_json",
        },
    }


def format_ranking_explain_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    paths = payload["config_paths"]
    lines.append("Config files (edit these to change behavior; then run `findmejobs rank`):")
    lines.append(f"  profile: {paths['profile']}")
    lines.append(f"  ranking: {paths['ranking']}")
    lines.append("")
    lines.append(f"rank_model_version: {payload['rank_model_version']}")
    rev = payload["review_eligibility"]
    lines.append(f"Review/export score floor: minimum_score={rev['minimum_score']} (plus hard filters).")
    lines.append("")
    lines.append("Hard-filter reason codes → config (see ranking/hard_filters.py):")
    for rule in payload["hard_filter_rules"]:
        cfg = ", ".join(rule["config"])
        lines.append(f"  {rule['reason']}: {cfg}")
        lines.append(f"    {rule['note']}")
    lines.append("")
    lines.append("Score components → weights in ranking.yaml (matched_signals = components with value > 0):")
    for row in payload["score_components"]:
        lines.append(f"  {row['component']}: weight {row['weight']}")
        lines.append(f"    inputs: {row['inputs']}")
    lines.append("")
    lines.append("Effective ranking policy (from merged profile + ranking.yaml):")
    lines.append(json.dumps(payload["ranking_policy"], indent=2))
    return "\n".join(lines) + "\n"
