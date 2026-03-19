from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from findmejobs.config.models import RankingWeights
from findmejobs.utils.text import collapse_whitespace


class ImportMetadata(BaseModel):
    import_id: str
    source_type: str
    original_filename: str
    stored_input_path: str
    extracted_text_path: str
    extracted_at: datetime
    original_sha256: str
    extracted_text_sha256: str
    char_count: int
    page_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    detected_links: list[str] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)
    extraction_pending: bool = False


class ProfileConfigDraft(BaseModel):
    version: str = "bootstrap-v1"
    full_name: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location_text: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    years_experience: int | None = None
    summary: str | None = None
    strengths: list[str] = Field(default_factory=list)
    recent_titles: list[str] = Field(default_factory=list)
    recent_companies: list[str] = Field(default_factory=list)
    target_titles: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    allowed_countries: list[str] = Field(default_factory=list)

    @field_validator(
        "full_name",
        "headline",
        "email",
        "phone",
        "location_text",
        "github_url",
        "linkedin_url",
        "summary",
        mode="before",
    )
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = collapse_whitespace(str(value))
        return cleaned or None

    @field_validator(
        "target_titles",
        "required_skills",
        "preferred_skills",
        "preferred_locations",
        "allowed_countries",
        "strengths",
        "recent_titles",
        "recent_companies",
        mode="before",
    )
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            raw = [value]
        else:
            raw = list(value)  # type: ignore[arg-type]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw:
            cleaned = collapse_whitespace(str(item))
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)
        return normalized

    @field_validator("years_experience", mode="before")
    @classmethod
    def _normalize_years_experience(cls, value: object) -> int | None:
        if value in (None, ""):
            return None
        return int(value)


class RankingConfigDraft(BaseModel):
    rank_model_version: str = "bootstrap-v1"
    stale_days: int = 30
    minimum_score: float = 45.0
    minimum_salary: int | None = None
    require_remote: bool | None = None
    remote_first: bool | None = None
    relocation_allowed: bool | None = None
    blocked_companies: list[str] | None = None
    blocked_title_keywords: list[str] | None = None
    allowed_companies: list[str] | None = None
    preferred_companies: list[str] | None = None
    preferred_timezones: list[str] | None = None
    title_families: dict[str, list[str]] | None = None
    weights: RankingWeights = Field(default_factory=RankingWeights)


class MissingFieldEntry(BaseModel):
    field: str
    reason: str
    required_for_promotion: bool


class MissingFieldsReport(BaseModel):
    missing: list[MissingFieldEntry] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)


class ResumeExtractionDraft(BaseModel):
    import_id: str | None = None
    full_name: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location_text: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    years_experience: int | None = None
    summary: str | None = None
    strengths: list[str] = Field(default_factory=list)
    recent_titles: list[str] = Field(default_factory=list)
    recent_companies: list[str] = Field(default_factory=list)
    target_titles: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    allowed_countries: list[str] = Field(default_factory=list)
    minimum_salary: int | None = None
    require_remote: bool | None = None
    relocation_allowed: bool | None = None
    blocked_companies: list[str] = Field(default_factory=list)
    blocked_title_keywords: list[str] = Field(default_factory=list)
    preferred_timezones: list[str] = Field(default_factory=list)
    title_families: dict[str, list[str]] = Field(default_factory=dict)
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    low_confidence_fields: list[str] = Field(default_factory=list)
    explicit_fields: list[str] = Field(default_factory=list)

    @field_validator(
        "full_name",
        "headline",
        "email",
        "phone",
        "location_text",
        "github_url",
        "linkedin_url",
        "summary",
        mode="before",
    )
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = collapse_whitespace(str(value))
        return cleaned or None

    @field_validator(
        "target_titles",
        "required_skills",
        "preferred_skills",
        "preferred_locations",
        "allowed_countries",
        "blocked_companies",
        "blocked_title_keywords",
        "preferred_timezones",
        "strengths",
        "recent_titles",
        "recent_companies",
        "low_confidence_fields",
        "explicit_fields",
        mode="before",
    )
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            raw = [value]
        else:
            raw = list(value)  # type: ignore[arg-type]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw:
            cleaned = collapse_whitespace(str(item))
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)
        return normalized

    @field_validator("title_families", mode="before")
    @classmethod
    def _normalize_title_families(cls, value: object) -> dict[str, list[str]]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise TypeError("title_families must be a mapping")
        normalized: dict[str, list[str]] = {}
        for family, titles in value.items():
            cleaned_family = collapse_whitespace(str(family))
            if not cleaned_family:
                continue
            normalized[cleaned_family] = ProfileConfigDraft._normalize_lists(titles)
        return normalized

    @field_validator("years_experience", mode="before")
    @classmethod
    def _normalize_years_experience(cls, value: object) -> int | None:
        if value in (None, ""):
            return None
        return int(value)


class ProfileExtractionPacket(BaseModel):
    import_id: str
    prompt_version: str
    instructions: str
    resume_text: str
    baseline_profile_draft: dict[str, object]
    baseline_ranking_draft: dict[str, object]
    baseline_missing_fields: dict[str, object]
    output_schema: dict[str, object]


class ProfileRefinementPacket(BaseModel):
    import_id: str
    prompt_version: str
    instructions: str
    current_profile_draft: dict[str, object]
    current_ranking_draft: dict[str, object]
    missing_fields: dict[str, object]
    user_answers: str
    output_schema: dict[str, object]


class DraftDiff(BaseModel):
    changed_fields: list[str] = Field(default_factory=list)
    new_fields: list[str] = Field(default_factory=list)
    protected_conflicts: list[str] = Field(default_factory=list)
    safe_auto_updates: list[str] = Field(default_factory=list)
    requires_manual_review: bool = False


class DraftValidationResult(BaseModel):
    status: Literal["failed", "minimal", "strong"]
    errors: list[str] = Field(default_factory=list)


class ImportPaths(BaseModel):
    state_root: Path
    input_path: Path
    extracted_text_path: Path
    extracted_meta_path: Path
    review_packet_path: Path
    review_result_path: Path
    refinement_packet_path: Path
    refinement_result_path: Path
    profile_draft_path: Path
    ranking_draft_path: Path
    missing_fields_path: Path
    import_report_path: Path
    raw_draft_response_path: Path
    diff_path: Path
    canonical_profile_path: Path
    canonical_ranking_path: Path
    history_root: Path
