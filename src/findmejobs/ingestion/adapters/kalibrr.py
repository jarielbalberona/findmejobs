from __future__ import annotations

import json
import re

from findmejobs.config.models import KalibrrSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import ParseStats, SourceAdapter, validate_config_type
from findmejobs.utils.urls import canonicalize_url


class KalibrrAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, KalibrrSourceConfig)
        return str(config.board_url)

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        return self.parse_with_stats(artifact, config)[0]

    def parse_with_stats(self, artifact: FetchArtifact, config: SourceConfig) -> tuple[list[SourceJobRecord], ParseStats]:
        validate_config_type(config, KalibrrSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = payload.get("jobs") or payload.get("data", {}).get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid_kalibrr_payload")
        records: list[SourceJobRecord] = []
        skipped_count = 0
        for job in jobs:
            source_url = canonicalize_url(job.get("job_url") or job.get("url"))
            job_id = str(job.get("id") or job.get("slug") or _slug_from_url(source_url or ""))
            title = str(job.get("title") or "").strip()
            company = _company_name(job, config.company_name)
            if not source_url or not job_id or not title:
                skipped_count += 1
                continue
            location = _location_text(job.get("locations"))
            salary_raw = _salary_text(job.get("salary"))
            tags = _list_text(job.get("tags")) + _list_text(job.get("departments"))
            records.append(
                SourceJobRecord(
                    source_job_key=job_id,
                    source_url=source_url,
                    apply_url=source_url,
                    title=title,
                    company=company,
                    location_text=location,
                    posted_at_raw=job.get("published_at") or job.get("updated_at"),
                    employment_type_raw=job.get("employment_type"),
                    description_raw=job.get("description") or job.get("summary"),
                    salary_raw=salary_raw,
                    tags_raw=tags,
                    raw_payload=job,
                )
            )
        if jobs and not records:
            raise ValueError("kalibrr_no_usable_jobs")
        return records, ParseStats(raw_seen_count=len(jobs), skipped_count=skipped_count)


def _company_name(job: dict, configured_company_name: str | None) -> str:
    company = job.get("company")
    if isinstance(company, dict):
        name = str(company.get("name") or "").strip()
        if name:
            return name
    return configured_company_name or "Unknown"


def _location_text(value: object) -> str:
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = str(item.get("name") or item.get("address") or "").strip()
            else:
                text = str(item).strip()
            if text:
                parts.append(text)
        return " / ".join(parts)
    if isinstance(value, str):
        return value.strip()
    return ""


def _salary_text(value: object) -> str | None:
    if isinstance(value, dict):
        minimum = _format_amount(value.get("from") or value.get("min"))
        maximum = _format_amount(value.get("to") or value.get("max"))
        currency = value.get("currency") or "PHP"
        period = value.get("period") or "month"
        if minimum or maximum:
            joined = minimum if maximum is None or minimum == maximum else f"{minimum} - {maximum}"
            return f"{currency} {joined} / {period}" if joined else None
    return None


def _format_amount(value: object) -> str | None:
    if value in (None, ""):
        return None
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return None


def _list_text(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("name") or item.get("label") or "").strip()
        else:
            text = str(item).strip()
        if text:
            result.append(text)
    return result


def _slug_from_url(value: str) -> str:
    match = re.search(r"/jobs/([^/?#]+)", value)
    return match.group(1) if match else ""
