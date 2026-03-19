from __future__ import annotations

from findmejobs.profile_bootstrap.models import DraftDiff, ProfileConfigDraft, RankingConfigDraft

PROTECTED_RANKING_FIELDS = {
    "minimum_salary",
    "require_remote",
    "relocation_allowed",
    "blocked_companies",
    "blocked_title_keywords",
}


def compare_drafts(
    current_profile: ProfileConfigDraft | None,
    current_ranking: RankingConfigDraft | None,
    draft_profile: ProfileConfigDraft,
    draft_ranking: RankingConfigDraft,
) -> DraftDiff:
    diff = DraftDiff()
    if current_profile is None:
        diff.new_fields.extend(_present_fields(draft_profile.model_dump()))
    else:
        _compare_mapping("profile", current_profile.model_dump(), draft_profile.model_dump(), diff)
    if current_ranking is None:
        diff.new_fields.extend(_present_fields(draft_ranking.model_dump()))
    else:
        _compare_mapping("ranking", current_ranking.model_dump(), draft_ranking.model_dump(), diff)
    diff.requires_manual_review = bool(diff.protected_conflicts)
    return diff


def _compare_mapping(prefix: str, current: dict, draft: dict, diff: DraftDiff) -> None:
    for key, draft_value in draft.items():
        current_value = current.get(key)
        field_name = f"{prefix}.{key}"
        if _is_empty(draft_value):
            continue
        if _is_empty(current_value):
            diff.new_fields.append(field_name)
            diff.safe_auto_updates.append(field_name)
            continue
        if current_value == draft_value:
            continue
        diff.changed_fields.append(field_name)
        if prefix == "ranking" and key in PROTECTED_RANKING_FIELDS:
            diff.protected_conflicts.append(field_name)
        else:
            diff.safe_auto_updates.append(field_name)


def _present_fields(values: dict) -> list[str]:
    return [key for key, value in values.items() if not _is_empty(value)]


def _is_empty(value: object) -> bool:
    return value in (None, "", []) or value == {}
