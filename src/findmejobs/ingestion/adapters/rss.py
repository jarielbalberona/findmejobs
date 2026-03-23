from __future__ import annotations

import feedparser

from findmejobs.config.models import RSSSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type
from findmejobs.utils.hashing import sha256_hexdigest


class RSSAdapter(SourceAdapter):
    transport_kind = "feed_xml"

    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, RSSSourceConfig)
        return str(config.feed_url)

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, RSSSourceConfig)
        parsed = feedparser.parse(artifact.body_bytes)
        records: list[SourceJobRecord] = []
        for entry in parsed.entries:
            source_url = getattr(entry, "link", None) or artifact.final_url
            title = getattr(entry, "title", "").strip()
            if not source_url or not title:
                continue
            source_job_key = (
                getattr(entry, "id", None)
                or getattr(entry, "guid", None)
                or canonical_rss_key(source_url, title)
            )
            tags = [tag.term for tag in getattr(entry, "tags", []) if getattr(tag, "term", None)]
            records.append(
                SourceJobRecord(
                    source_job_key=source_job_key,
                    source_url=source_url,
                    apply_url=source_url,
                    title=title,
                    company=_extract_company(entry),
                    location_text=_extract_location(entry),
                    posted_at_raw=getattr(entry, "published", None) or getattr(entry, "updated", None),
                    description_raw=getattr(entry, "summary", None) or getattr(entry, "description", None),
                    tags_raw=tags,
                    raw_payload={
                        "title": getattr(entry, "title", None),
                        "link": source_url,
                        "summary": getattr(entry, "summary", None),
                        "published": getattr(entry, "published", None),
                        "tags": tags,
                    },
                )
            )
        return records


def canonical_rss_key(source_url: str, title: str) -> str:
    return sha256_hexdigest(f"{source_url}|{title}")[:24]


def _extract_company(entry) -> str:  # type: ignore[no-untyped-def]
    author = getattr(entry, "author", None)
    if author:
        return str(author).strip()
    for key in ("company", "publisher"):
        value = getattr(entry, key, None)
        if value:
            return str(value).strip()
    return "Unknown"


def _extract_location(entry) -> str:  # type: ignore[no-untyped-def]
    for key in ("location", "where", "job_location"):
        value = getattr(entry, key, None)
        if value:
            return str(value).strip()
    return ""
