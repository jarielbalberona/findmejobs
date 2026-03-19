from __future__ import annotations

from pathlib import Path
import shutil

from findmejobs.profile_bootstrap.diff import compare_drafts
from findmejobs.profile_bootstrap.models import DraftDiff, ProfileConfigDraft, RankingConfigDraft
from findmejobs.utils.yamlio import dump_yaml, load_yaml


def load_existing_profile(path: Path) -> ProfileConfigDraft | None:
    if not path.exists():
        return None
    return ProfileConfigDraft.model_validate(load_yaml(path))


def load_existing_ranking(path: Path) -> RankingConfigDraft | None:
    if not path.exists():
        return None
    return RankingConfigDraft.model_validate(load_yaml(path))


def promote_drafts(
    profile_path: Path,
    ranking_path: Path,
    profile_draft: ProfileConfigDraft,
    ranking_draft: RankingConfigDraft,
) -> DraftDiff:
    existing_profile = load_existing_profile(profile_path)
    existing_ranking = load_existing_ranking(ranking_path)
    diff = compare_drafts(existing_profile, existing_ranking, profile_draft, ranking_draft)
    if diff.protected_conflicts:
        raise ValueError(f"protected_preferences_conflict:{','.join(diff.protected_conflicts)}")
    merged_profile = _merge_profile(existing_profile, profile_draft)
    merged_ranking = _merge_ranking(existing_ranking, ranking_draft)
    dump_yaml(merged_profile.model_dump(mode="json"), profile_path)
    dump_yaml(merged_ranking.model_dump(mode="json"), ranking_path)
    return diff


def snapshot_canonical_config(profile_path: Path, ranking_path: Path, snapshot_root: Path) -> Path | None:
    snapshot_root.mkdir(parents=True, exist_ok=True)
    copied = False
    if profile_path.exists():
        shutil.copy2(profile_path, snapshot_root / profile_path.name)
        copied = True
    if ranking_path.exists():
        shutil.copy2(ranking_path, snapshot_root / ranking_path.name)
        copied = True
    if not copied:
        return None
    return snapshot_root


def _merge_profile(existing: ProfileConfigDraft | None, draft: ProfileConfigDraft) -> ProfileConfigDraft:
    if existing is None:
        return draft
    payload = existing.model_dump()
    for key, value in draft.model_dump().items():
        if value in (None, "", []):
            continue
        payload[key] = value
    return ProfileConfigDraft.model_validate(payload)


def _merge_ranking(existing: RankingConfigDraft | None, draft: RankingConfigDraft) -> RankingConfigDraft:
    if existing is None:
        return draft
    payload = existing.model_dump()
    for key, value in draft.model_dump().items():
        if key in {"minimum_salary", "require_remote", "relocation_allowed", "blocked_companies", "blocked_title_keywords"}:
            if value in (None, [], ""):
                continue
            if payload.get(key) not in (None, [], "") and payload.get(key) != value:
                continue
        elif value in (None, "", []):
            continue
        payload[key] = value
    return RankingConfigDraft.model_validate(payload)
