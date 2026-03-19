"""Safe, validated updates to operator ranking.yaml (scalars only)."""

from __future__ import annotations

from pathlib import Path

from findmejobs.profile_bootstrap.models import RankingConfigDraft
from findmejobs.utils.yamlio import dump_yaml, load_yaml


def patch_ranking_yaml(
    path: Path,
    *,
    stale_days: int | None = None,
    minimum_score: float | None = None,
    minimum_salary: int | None = None,
    clear_minimum_salary: bool = False,
    rank_model_version: str | None = None,
    require_remote: bool | None = None,
    remote_first: bool | None = None,
) -> RankingConfigDraft:
    """Merge CLI overrides into existing ranking.yaml and rewrite the file."""
    raw = load_yaml(path)
    if not isinstance(raw, dict):
        raw = {}

    if stale_days is not None:
        raw["stale_days"] = stale_days
    if minimum_score is not None:
        raw["minimum_score"] = minimum_score
    if clear_minimum_salary:
        raw["minimum_salary"] = None
    elif minimum_salary is not None:
        raw["minimum_salary"] = minimum_salary
    if rank_model_version is not None:
        raw["rank_model_version"] = rank_model_version.strip()
    if require_remote is not None:
        raw["require_remote"] = require_remote
    if remote_first is not None:
        raw["remote_first"] = remote_first

    draft = RankingConfigDraft.model_validate(raw)
    dump_yaml(draft.model_dump(mode="json"), path)
    return draft
