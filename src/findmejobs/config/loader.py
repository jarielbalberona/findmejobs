from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Iterable

from pydantic import TypeAdapter

from findmejobs.config.models import AppConfig, ProfileConfig, RankingPolicy, SourceConfig
from findmejobs.profile_bootstrap.models import ProfileConfigDraft, RankingConfigDraft
from findmejobs.utils.yamlio import load_yaml


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_app_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(_read_toml(path))


def load_profile_config(path: Path) -> ProfileConfig:
    resolved = resolve_profile_config_path(path)
    if resolved.suffix.casefold() in {".yaml", ".yml"}:
        return _load_yaml_profile_config(resolved)
    return ProfileConfig.model_validate(_read_toml(resolved))


def load_source_configs(directory: Path) -> list[SourceConfig]:
    adapter = TypeAdapter(SourceConfig)
    sources: list[SourceConfig] = []
    for path in sorted(directory.glob("*.toml")):
        sources.append(adapter.validate_python(_read_toml(path)))
    return sources


def ensure_directories(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def resolve_profile_config_path(path: Path) -> Path:
    if path.exists():
        return path
    if path.suffix.casefold() == ".toml":
        yaml_path = path.with_suffix(".yaml")
        if yaml_path.exists():
            return yaml_path
    raise FileNotFoundError(path)


def _load_yaml_profile_config(profile_path: Path) -> ProfileConfig:
    ranking_path = profile_path.with_name("ranking.yaml")
    if not ranking_path.exists():
        raise FileNotFoundError(ranking_path)
    profile = ProfileConfigDraft.model_validate(load_yaml(profile_path))
    ranking = RankingConfigDraft.model_validate(load_yaml(ranking_path))
    return ProfileConfig(
        version=profile.version,
        rank_model_version=ranking.rank_model_version,
        full_name=profile.full_name,
        headline=profile.headline,
        email=profile.email,
        phone=profile.phone,
        location_text=profile.location_text,
        github_url=profile.github_url,
        linkedin_url=profile.linkedin_url,
        years_experience=profile.years_experience,
        summary=profile.summary,
        strengths=profile.strengths,
        recent_titles=profile.recent_titles,
        recent_companies=profile.recent_companies,
        target_titles=profile.target_titles,
        required_skills=profile.required_skills,
        preferred_skills=profile.preferred_skills,
        preferred_locations=profile.preferred_locations,
        allowed_countries=profile.allowed_countries,
        ranking=RankingPolicy(
            stale_days=ranking.stale_days,
            minimum_score=ranking.minimum_score,
            minimum_salary=ranking.minimum_salary,
            blocked_companies=ranking.blocked_companies or [],
            blocked_title_keywords=ranking.blocked_title_keywords or [],
            require_remote=bool(ranking.require_remote),
            remote_first=bool(ranking.remote_first),
            allowed_countries=profile.allowed_countries,
            allowed_companies=ranking.allowed_companies or [],
            preferred_companies=ranking.preferred_companies or [],
            preferred_timezones=ranking.preferred_timezones or [],
            title_families=ranking.title_families or {},
            weights=ranking.weights,
        ),
        application={
            "professional_summary": profile.summary,
        },
    )
