from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Iterable

from pydantic import TypeAdapter

from findmejobs.config.models import AppConfig, ProfileConfig, SourceConfig


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_app_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(_read_toml(path))


def load_profile_config(path: Path) -> ProfileConfig:
    return ProfileConfig.model_validate(_read_toml(path))


def load_source_configs(directory: Path) -> list[SourceConfig]:
    adapter = TypeAdapter(SourceConfig)
    sources: list[SourceConfig] = []
    for path in sorted(directory.glob("*.toml")):
        sources.append(adapter.validate_python(_read_toml(path)))
    return sources


def ensure_directories(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
