from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from findmejobs.config.loader import load_app_config, load_profile_config, load_source_configs


def test_valid_config_loads_correctly(runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, sources_dir = runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    sources = load_source_configs(sources_dir)

    assert app_config.database.url.startswith("sqlite:///")
    assert profile.version == "test-profile"
    assert {source.kind for source in sources} == {"rss", "greenhouse"}


def test_invalid_source_config_fails_clearly(tmp_path: Path) -> None:
    sources_dir = tmp_path / "sources.d"
    sources_dir.mkdir(parents=True)
    (sources_dir / "bad.toml").write_text(
        '\n'.join(['name = "bad"', 'kind = "not-real"', "enabled = true"]),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc:
        load_source_configs(sources_dir)

    assert "not-real" in str(exc.value)


def test_missing_required_fields_are_surfaced(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.toml"
    profile_path.write_text('version = "v1"\nrank_model_version = "r1"\n', encoding="utf-8")

    with pytest.raises(ValidationError) as exc:
        load_profile_config(profile_path)

    assert "target_titles" in str(exc.value)


def test_yaml_profile_pair_loads_correctly(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.yaml"
    ranking_path = tmp_path / "ranking.yaml"
    profile_path.write_text(
        '{"version":"bootstrap-v1","full_name":"Jane Doe","target_titles":["Backend Engineer"],"required_skills":["Python"],"preferred_locations":["Remote"],"allowed_countries":["PH"]}',
        encoding="utf-8",
    )
    ranking_path.write_text(
        '{"rank_model_version":"bootstrap-v1","stale_days":30,"minimum_score":45.0,"require_remote":true,"weights":{"title_alignment":30.0,"must_have_skills":35.0,"preferred_skills":10.0,"location_fit":10.0,"remote_fit":10.0,"recency":5.0}}',
        encoding="utf-8",
    )

    profile = load_profile_config(profile_path)

    assert profile.target_titles == ["Backend Engineer"]
    assert profile.ranking.require_remote is True
