from __future__ import annotations

import re

from findmejobs.profile_bootstrap.models import MissingFieldEntry, MissingFieldsReport, ProfileConfigDraft, RankingConfigDraft

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
    if not profile.email:
        missing.append(
            MissingFieldEntry(
                field="email",
                reason="email was not confidently extracted",
                required_for_promotion=False,
            )
        )
    return MissingFieldsReport(missing=missing, low_confidence_fields=sorted(set(low_confidence_fields)))


def validate_drafts(profile: ProfileConfigDraft, ranking: RankingConfigDraft, missing: MissingFieldsReport) -> list[str]:
    errors: list[str] = []
    if not profile.version:
        errors.append("invalid_profile_version")
    if not ranking.rank_model_version:
        errors.append("invalid_rank_model_version")
    if profile.email and not EMAIL_RE.match(profile.email):
        errors.append("invalid_email")
    if any(item.required_for_promotion for item in missing.missing):
        errors.append("missing_required_fields")
    if ranking.minimum_salary is not None and ranking.minimum_salary <= 0:
        errors.append("invalid_minimum_salary")
    if ranking.stale_days <= 0:
        errors.append("invalid_stale_days")
    if ranking.minimum_score < 0 or ranking.minimum_score > 100:
        errors.append("invalid_minimum_score")
    return errors
