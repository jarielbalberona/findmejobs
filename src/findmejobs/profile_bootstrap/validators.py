from __future__ import annotations

import re

from findmejobs.profile_bootstrap.models import (
    DraftValidationResult,
    MissingFieldEntry,
    MissingFieldsReport,
    ProfileConfigDraft,
    RankingConfigDraft,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def build_missing_fields_report(profile: ProfileConfigDraft, low_confidence_fields: list[str]) -> MissingFieldsReport:
    missing: list[MissingFieldEntry] = []
    if not profile.target_titles:
        missing.append(
            MissingFieldEntry(
                field="target_titles",
                reason="at least one target title is required for promotion",
                required_for_promotion=True,
            )
        )
    if not profile.required_skills and not profile.preferred_skills:
        missing.append(
            MissingFieldEntry(
                field="skills",
                reason="at least one required or preferred skill is required for promotion",
                required_for_promotion=True,
            )
        )
    if not profile.location_text and not profile.preferred_locations and not profile.allowed_countries:
        missing.append(
            MissingFieldEntry(
                field="location_preferences",
                reason="at least one location signal is required for promotion",
                required_for_promotion=True,
            )
        )
    if not profile.full_name:
        missing.append(
            MissingFieldEntry(
                field="full_name",
                reason="name was not confidently extracted",
                required_for_promotion=False,
            )
        )
    if not profile.summary:
        missing.append(
            MissingFieldEntry(
                field="summary",
                reason="short professional summary was not confidently extracted",
                required_for_promotion=False,
            )
        )
    if not profile.email:
        missing.append(
            MissingFieldEntry(
                field="email",
                reason="email was not confidently extracted",
                required_for_promotion=False,
            )
        )
    return MissingFieldsReport(missing=missing, low_confidence_fields=sorted(set(low_confidence_fields)))


def validate_drafts(
    profile: ProfileConfigDraft,
    ranking: RankingConfigDraft,
    missing: MissingFieldsReport,
    *,
    source_char_count: int | None = None,
) -> DraftValidationResult:
    errors: list[str] = []
    if not profile.version:
        errors.append("invalid_profile_version")
    if not ranking.rank_model_version:
        errors.append("invalid_rank_model_version")
    if profile.email and not EMAIL_RE.match(profile.email):
        errors.append("invalid_email")
    if ranking.minimum_salary is not None and ranking.minimum_salary <= 0:
        errors.append("invalid_minimum_salary")
    if ranking.stale_days <= 0:
        errors.append("invalid_stale_days")
    if ranking.minimum_score < 0 or ranking.minimum_score > 100:
        errors.append("invalid_minimum_score")
    if _is_practically_empty(profile):
        errors.append("draft_practically_empty")
    if source_char_count is not None and source_char_count >= 2000 and _is_underpopulated_for_rich_resume(profile):
        errors.append("rich_resume_underpopulated")
    if errors:
        return DraftValidationResult(status="failed", errors=errors)
    if any(item.required_for_promotion for item in missing.missing):
        return DraftValidationResult(status="minimal", errors=["missing_required_fields"])
    return DraftValidationResult(status="strong", errors=[])


def _is_practically_empty(profile: ProfileConfigDraft) -> bool:
    meaningful_signals = [
        profile.full_name,
        profile.headline,
        profile.email,
        profile.phone,
        profile.location_text,
        profile.github_url,
        profile.linkedin_url,
        profile.summary,
        profile.target_titles,
        profile.required_skills,
        profile.preferred_skills,
        profile.strengths,
        profile.recent_titles,
        profile.recent_companies,
    ]
    populated = sum(1 for signal in meaningful_signals if signal not in (None, "", []))
    return populated < 4


def _is_underpopulated_for_rich_resume(profile: ProfileConfigDraft) -> bool:
    enough_contacts = bool(profile.email or profile.phone or profile.github_url or profile.linkedin_url)
    enough_direction = bool(profile.target_titles and (profile.required_skills or profile.preferred_skills))
    enough_context = bool(profile.summary or profile.headline or profile.recent_titles)
    return not (enough_contacts and enough_direction and enough_context and profile.location_text)
