from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

import httpx

from findmejobs.config.models import SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord


@dataclass(slots=True)
class ParseStats:
    raw_seen_count: int
    skipped_count: int = 0


class SourceAdapter(ABC):
    @abstractmethod
    def build_url(self, config: SourceConfig) -> str:
        raise NotImplementedError

    @abstractmethod
    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        raise NotImplementedError

    def parse_with_stats(self, artifact: FetchArtifact, config: SourceConfig) -> tuple[list[SourceJobRecord], ParseStats]:
        records = self.parse(artifact, config)
        return records, ParseStats(raw_seen_count=len(records), skipped_count=0)


class AdapterFactory(Protocol):
    def __call__(self, config: SourceConfig) -> SourceAdapter: ...


def build_default_headers(user_agent: str) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept": "*/*"}


def validate_config_type(config: SourceConfig, expected: type) -> None:
    if not isinstance(config, expected):
        raise TypeError(f"expected {expected.__name__}, got {type(config).__name__}")
