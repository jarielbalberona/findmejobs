from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

import httpx

from findmejobs.config.models import SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord, TransportKind


@dataclass(slots=True)
class ParseStats:
    raw_seen_count: int
    skipped_count: int = 0


class SourceAdapter(ABC):
    transport_kind: TransportKind = "api_json"

    @abstractmethod
    def build_url(self, config: SourceConfig) -> str:
        raise NotImplementedError

    @abstractmethod
    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        raise NotImplementedError

    def parse_with_stats(self, artifact: FetchArtifact, config: SourceConfig) -> tuple[list[SourceJobRecord], ParseStats]:
        records = self.parse(artifact, config)
        return records, ParseStats(raw_seen_count=len(records), skipped_count=0)

    def build_headers(self, config: SourceConfig) -> dict[str, str]:
        del config
        return {"Accept": _accept_header_for_transport(self.transport_kind)}


class AdapterFactory(Protocol):
    def __call__(self, config: SourceConfig) -> SourceAdapter: ...


def build_default_headers(user_agent: str) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept": "*/*"}


def _accept_header_for_transport(transport_kind: TransportKind) -> str:
    if transport_kind == "api_json":
        return "application/json, text/json;q=0.9, */*;q=0.1"
    if transport_kind == "feed_xml":
        return "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"
    return "text/html, application/xhtml+xml;q=0.9, */*;q=0.1"


def validate_config_type(config: SourceConfig, expected: type) -> None:
    if not isinstance(config, expected):
        raise TypeError(f"expected {expected.__name__}, got {type(config).__name__}")
