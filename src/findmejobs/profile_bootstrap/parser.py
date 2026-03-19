from __future__ import annotations

from findmejobs.profile_bootstrap.models import ProfileConfigDraft, RankingConfigDraft, ResumeExtractionDraft

EXPLICIT_HARD_PREFERENCE_FIELDS = {
    "minimum_salary",
    "require_remote",
    "relocation_allowed",
    "blocked_companies",
    "blocked_title_keywords",
}


def build_profile_draft(extraction: ResumeExtractionDraft) -> ProfileConfigDraft:
    return ProfileConfigDraft(
        full_name=extraction.full_name,
        email=extraction.email,
        location_text=extraction.location_text,
        target_titles=extraction.target_titles,
        required_skills=extraction.required_skills,
        preferred_skills=extraction.preferred_skills,
        preferred_locations=extraction.preferred_locations,
        allowed_countries=extraction.allowed_countries,
    )


def build_ranking_draft(extraction: ResumeExtractionDraft) -> RankingConfigDraft:
    explicit_fields = set(extraction.explicit_fields)
    return RankingConfigDraft(
        minimum_salary=extraction.minimum_salary if "minimum_salary" in explicit_fields else None,
        require_remote=extraction.require_remote if "require_remote" in explicit_fields else None,
        relocation_allowed=extraction.relocation_allowed if "relocation_allowed" in explicit_fields else None,
        blocked_companies=extraction.blocked_companies if "blocked_companies" in explicit_fields else None,
        blocked_title_keywords=extraction.blocked_title_keywords if "blocked_title_keywords" in explicit_fields else None,
    )


def merge_extraction_drafts(base: ResumeExtractionDraft, refined: ResumeExtractionDraft) -> ResumeExtractionDraft:
    payload = base.model_dump(mode="python")
    refined_payload = refined.model_dump(mode="python")
    for key, value in refined_payload.items():
        if key == "import_id":
            continue
        if isinstance(value, list):
            if not value:
                continue
            payload[key] = value
            continue
        if isinstance(value, dict):
            if not value:
                continue
            merged = dict(payload.get(key) or {})
            for field_name, evidence in value.items():
                merged[field_name] = evidence
            payload[key] = merged
            continue
        if value in (None, ""):
            continue
        payload[key] = value
    payload["import_id"] = base.import_id or refined.import_id
    payload["low_confidence_fields"] = _merge_lists(base.low_confidence_fields, refined.low_confidence_fields)
    payload["explicit_fields"] = _merge_lists(base.explicit_fields, refined.explicit_fields)
    return ResumeExtractionDraft.model_validate(payload)


def _merge_lists(left: list[str], right: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged
