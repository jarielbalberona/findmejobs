from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Iterable

from findmejobs.config.models import AppConfig, ApplicationProfile, ProfileConfig, RankingPolicy, SourceConfig, SourcesFileConfig
from findmejobs.profile_bootstrap.models import ProfileConfigDraft, RankingConfigDraft
from findmejobs.utils.yamlio import load_yaml


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_app_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(_read_toml(path))


def load_profile_config(path: Path) -> ProfileConfig:
    resolved = resolve_profile_config_path(path)
    if resolved.suffix.casefold() not in {".yaml", ".yml"}:
        raise ValueError(f"profile config must be YAML: {resolved}")
    return _load_yaml_profile_config(resolved)


def load_source_configs(path: Path) -> list[SourceConfig]:
    if path.suffix.casefold() in {".yaml", ".yml"}:
        if not path.exists():
            return []
        raw = load_yaml(path)
        config = SourcesFileConfig.model_validate(raw or {})
        return config.sources

    if not path.exists():
        return []
    raise ValueError(f"unsupported source config path (expected YAML file): {path}")


def ensure_directories(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def resolve_profile_config_path(path: Path) -> Path:
    if path.exists():
        return path
    raise FileNotFoundError(path)


def _load_yaml_profile_config(profile_path: Path) -> ProfileConfig:
    ranking_path = profile_path.with_name("ranking.yaml")
    if not ranking_path.exists():
        raise FileNotFoundError(ranking_path)
    raw_profile = _load_yaml_mapping_or_legacy_toml(profile_path)
    profile = ProfileConfigDraft.model_validate(raw_profile)

    raw_ranking = _load_yaml_mapping_or_legacy_toml(ranking_path)
    ranking = RankingConfigDraft.model_validate(raw_ranking)
    application_data: dict = {}
    if profile.application is not None:
        application_data = profile.application.model_dump(mode="python", exclude_none=True)
    elif isinstance(raw_profile, dict) and isinstance(raw_profile.get("application"), dict):
        application_data = dict(raw_profile["application"])
    if not application_data.get("professional_summary") and profile.summary:
        application_data = {**application_data, "professional_summary": profile.summary}
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
        application=ApplicationProfile.model_validate(application_data),
    )


def _load_yaml_mapping_or_legacy_toml(path: Path) -> dict:
    raw = load_yaml(path)
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    # Backward compatibility for legacy TOML payloads written to YAML paths.
    text = path.read_text(encoding="utf-8")
    return tomllib.loads(text)
