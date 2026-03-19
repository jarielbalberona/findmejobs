from __future__ import annotations

import json

from lxml import html

from findmejobs.config.models import DirectPageSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.text import collapse_whitespace, html_to_text
from findmejobs.utils.urls import canonicalize_url


class DirectPageAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, DirectPageSourceConfig)
        return str(config.page_url)

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, DirectPageSourceConfig)
        body = artifact.body_bytes.decode("utf-8", errors="replace")
        extracted = extract_direct_page_job(body, artifact.final_url, config.company_name)
        if extracted is None:
            raise ValueError("direct_page_no_job_data")
        return [extracted]


def extract_direct_page_job(page_html: str, source_url: str, configured_company: str | None = None) -> SourceJobRecord | None:
    jsonld = _extract_jobposting_jsonld(page_html)
    if jsonld is not None:
        record = _record_from_jsonld(jsonld, source_url, configured_company)
        if record is not None:
            return record
    return _record_from_fallback(page_html, source_url, configured_company)


def _extract_jobposting_jsonld(page_html: str) -> dict | None:
    try:
        root = html.fromstring(page_html)
    except (html.ParserError, ValueError):
        return None
    scripts = root.xpath("//script[@type='application/ld+json']/text()")
    for script in scripts:
        try:
            payload = json.loads(script)
        except json.JSONDecodeError:
            continue
        for item in _flatten_jsonld(payload):
            if isinstance(item, dict) and str(item.get("@type", "")).casefold() == "jobposting":
                return item
    return None


def _flatten_jsonld(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("@graph"), list):
            return payload["@graph"]
        return [payload]
    return []


def _record_from_jsonld(payload: dict, source_url: str, configured_company: str | None) -> SourceJobRecord | None:
    title = collapse_whitespace(str(payload.get("title", "") or payload.get("name", "")))
    company = configured_company or _jsonld_company_name(payload)
    apply_url = canonicalize_url(payload.get("url")) or canonicalize_url(source_url)
    if not title or not apply_url:
        return None
    location_text = _jsonld_location_text(payload)
    description = payload.get("description")
    tags: list[str] = []
    category = payload.get("industry") or payload.get("employmentType")
    if isinstance(category, str) and category.strip():
        tags.append(category.strip())
    return SourceJobRecord(
        source_job_key=str(payload.get("identifier") or payload.get("jobId") or sha256_hexdigest(apply_url)[:24]),
        source_url=source_url,
        apply_url=apply_url,
        title=title,
        company=company or "Unknown",
        location_text=location_text,
        posted_at_raw=payload.get("datePosted"),
        employment_type_raw=payload.get("employmentType"),
        description_raw=description,
        tags_raw=tags,
        raw_payload={"json_ld": payload},
    )


def _jsonld_company_name(payload: dict) -> str | None:
    hiring_org = payload.get("hiringOrganization")
    if isinstance(hiring_org, dict):
        name = collapse_whitespace(str(hiring_org.get("name", "")))
        return name or None
    return None


def _jsonld_location_text(payload: dict) -> str:
    locations = payload.get("jobLocation")
    if isinstance(locations, dict):
        locations = [locations]
    parts: list[str] = []
    if isinstance(locations, list):
        for location in locations:
            if not isinstance(location, dict):
                continue
            address = location.get("address") if isinstance(location.get("address"), dict) else {}
            text = ", ".join(
                str(value)
                for value in [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
                if value
            )
            if text:
                parts.append(text)
    return " / ".join(parts)


def _record_from_fallback(page_html: str, source_url: str, configured_company: str | None) -> SourceJobRecord | None:
    try:
        root = html.fromstring(page_html)
    except (html.ParserError, ValueError):
        return None
    title = (
        _first_text(root, "//meta[@property='og:title']/@content")
        or _first_text(root, "//h1/text()")
        or _first_text(root, "//title/text()")
        or ""
    )
    title = title.split("|")[0].split(" - ")[0].strip()
    if not title:
        return None
    company = configured_company or _first_text(root, "//meta[@property='og:site_name']/@content") or "Unknown"
    location = (
        _first_text(root, "//*[@itemprop='jobLocation']//text()")
        or _first_text(root, "//*[contains(@class,'location')]//text()")
        or ""
    )
    date_posted = _first_text(root, "//*[@itemprop='datePosted']/@content") or _first_text(root, "//time/@datetime")
    description_node = root.xpath("//*[contains(@class,'description') or contains(@class,'job-description') or @id='job-description']")
    description_html = html.tostring(description_node[0], encoding="unicode") if description_node else page_html
    apply_url = canonicalize_url(source_url)
    if apply_url is None:
        return None
    return SourceJobRecord(
        source_job_key=sha256_hexdigest(apply_url)[:24],
        source_url=source_url,
        apply_url=apply_url,
        title=title,
        company=collapse_whitespace(company),
        location_text=collapse_whitespace(location),
        posted_at_raw=date_posted,
        description_raw=description_html,
        tags_raw=_fallback_tags(title, html_to_text(description_html)),
        raw_payload={"fallback": True},
    )


def _first_text(root, xpath_expr: str) -> str | None:  # type: ignore[no-untyped-def]
    values = root.xpath(xpath_expr)
    for value in values:
        cleaned = collapse_whitespace(str(value))
        if cleaned:
            return cleaned
    return None


def _fallback_tags(title: str, description: str) -> list[str]:
    haystack = f"{title} {description}".casefold()
    tags = []
    for keyword in ("python", "sql", "aws", "remote", "backend", "frontend"):
        if keyword in haystack:
            tags.append(keyword)
    return tags
