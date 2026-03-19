"""Read/write validated source definitions in canonical `config/sources.yaml`."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from findmejobs.config.models import SourceConfig, SourcesFileConfig
from findmejobs.utils.yamlio import dump_yaml, load_yaml

_source_adapter = TypeAdapter(SourceConfig)


def parse_source_json_payload(raw: str) -> SourceConfig:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json:{exc}") from exc
    if not isinstance(body, dict):
        raise ValueError("json_must_be_object")
    try:
        return _source_adapter.validate_python(body)
    except ValidationError as exc:
        raise ValueError(f"validation_error:{exc}") from exc


def load_sources_file(path: Path) -> SourcesFileConfig:
    if not path.exists():
        return SourcesFileConfig()
    raw = load_yaml(path)
    return SourcesFileConfig.model_validate(raw or {})


def write_sources_file(path: Path, config: SourcesFileConfig) -> None:
    dump_yaml(config.model_dump(mode="json"), path)


def list_sources(path: Path) -> list[SourceConfig]:
    return load_sources_file(path).sources


def add_source(path: Path, source: SourceConfig, *, replace: bool = False) -> SourcesFileConfig:
    config = load_sources_file(path)
    idx = _find_source_index(config.sources, source.name)
    if idx is not None and not replace:
        raise ValueError(f"source_already_exists:{source.name}")
    if idx is None:
        config.sources.append(source)
    else:
        config.sources[idx] = source
    write_sources_file(path, config)
    return config


def set_source_fields(
    path: Path,
    *,
    name: str,
    enabled: bool | None = None,
    priority: int | None = None,
    trust_weight: float | None = None,
    fetch_cap: int | None = None,
    add_blocked_title_keywords: list[str] | None = None,
    remove_blocked_title_keywords: list[str] | None = None,
) -> SourceConfig:
    config = load_sources_file(path)
    idx = _find_source_index(config.sources, name)
    if idx is None:
        raise ValueError(f"unknown_source:{name}")
    source = config.sources[idx]
    payload = source.model_dump(mode="python")
    if enabled is not None:
        payload["enabled"] = enabled
    if priority is not None:
        payload["priority"] = priority
    if trust_weight is not None:
        payload["trust_weight"] = trust_weight
    if fetch_cap is not None:
        payload["fetch_cap"] = fetch_cap
    blocked = list(payload.get("blocked_title_keywords") or [])
    if add_blocked_title_keywords:
        blocked.extend(add_blocked_title_keywords)
    if remove_blocked_title_keywords:
        remove = {item.casefold() for item in remove_blocked_title_keywords}
        blocked = [item for item in blocked if item.casefold() not in remove]
    payload["blocked_title_keywords"] = _dedupe(blocked)
    updated = _source_adapter.validate_python(payload)
    config.sources[idx] = updated
    write_sources_file(path, config)
    return updated


def disable_source(path: Path, *, name: str) -> SourceConfig:
    return set_source_fields(path, name=name, enabled=False)


def remove_source(path: Path, *, name: str) -> SourcesFileConfig:
    config = load_sources_file(path)
    idx = _find_source_index(config.sources, name)
    if idx is None:
        raise ValueError(f"unknown_source:{name}")
    config.sources.pop(idx)
    write_sources_file(path, config)
    return config


def _find_source_index(sources: list[SourceConfig], name: str) -> int | None:
    wanted = name.casefold()
    for idx, source in enumerate(sources):
        if source.name.casefold() == wanted:
            return idx
    return None


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = str(item).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out
