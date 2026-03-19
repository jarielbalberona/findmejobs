from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

import httpx

from findmejobs.config.models import GreenhouseSourceConfig, RSSSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord


class SourceAdapter(ABC):
    @abstractmethod
    def build_url(self, config: SourceConfig) -> str:
        raise NotImplementedError

    @abstractmethod
    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        raise NotImplementedError


class AdapterFactory(Protocol):
    def __call__(self, config: SourceConfig) -> SourceAdapter: ...


def build_default_headers(user_agent: str) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept": "*/*"}


def validate_config_type(config: SourceConfig, expected: type[RSSSourceConfig] | type[GreenhouseSourceConfig]) -> None:
    if not isinstance(config, expected):
        raise TypeError(f"expected {expected.__name__}, got {type(config).__name__}")
