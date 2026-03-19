"""Write validated source definitions to `config/sources.d/*.toml`."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import tomli_w
from pydantic import TypeAdapter, ValidationError

from findmejobs.config.models import SourceConfig

_source_adapter = TypeAdapter(SourceConfig)


def safe_source_filename_stem(name: str) -> str:
    """Filesystem-safe stem derived from source `name` (lowercase, no path chars)."""
    s = name.strip().lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9_.-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "source"


def source_config_to_toml_document(config: SourceConfig) -> dict:
    """Turn a validated config into a TOML-friendly dict (JSON mode for URLs etc.)."""
    return config.model_dump(mode="json", exclude_none=True)


def write_source_toml(path: Path, config: SourceConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = source_config_to_toml_document(config)
    path.write_text(tomli_w.dumps(doc).rstrip() + "\n", encoding="utf-8")


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


def find_toml_path_for_source_name(sources_dir: Path, name: str) -> Path | None:
    """Return the first `*.toml` under `sources_dir` whose top-level `name` matches."""
    if not sources_dir.is_dir():
        return None
    for path in sorted(sources_dir.glob("*.toml")):
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(data, dict) and data.get("name") == name:
            return path
    return None


def resolve_output_path(
    sources_dir: Path,
    config: SourceConfig,
    *,
    output_stem: str | None = None,
) -> Path:
    base = (output_stem or safe_source_filename_stem(config.name)).strip()
    if not base or base in {".", ".."} or "/" in base or "\\" in base:
        raise ValueError("invalid_output_stem")
    return sources_dir / f"{base}.toml"


def iter_validated_source_files(sources_dir: Path):
    """Yield `(path, config)` for each `*.toml`, validating with `SourceConfig`."""
    if not sources_dir.is_dir():
        return
    for path in sorted(sources_dir.glob("*.toml")):
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        yield path, _source_adapter.validate_python(raw)
