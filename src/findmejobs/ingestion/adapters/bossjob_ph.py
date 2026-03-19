from __future__ import annotations

import json
import re

from findmejobs.config.models import BossjobPHSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import ParseStats, SourceAdapter, validate_config_type
from findmejobs.utils.urls import canonicalize_url


class BossjobPHAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, BossjobPHSourceConfig)
        return str(config.board_url)

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        return self.parse_with_stats(artifact, config)[0]

    def parse_with_stats(self, artifact: FetchArtifact, config: SourceConfig) -> tuple[list[SourceJobRecord], ParseStats]:
        validate_config_type(config, BossjobPHSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = payload.get("data", {}).get("jobs") if isinstance(payload.get("data"), dict) else payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid_bossjob_ph_payload")
        records: list[SourceJobRecord] = []
        skipped_count = 0
        for job in jobs:
            source_url = canonicalize_url(job.get("job_url") or job.get("url"))
            job_id = str(job.get("job_id") or job.get("id") or _job_id_from_url(source_url or ""))
            title = str(job.get("job_name") or job.get("title") or "").strip()
            company = _company_name(job, config.company_name)
            if not source_url or not job_id or not title:
                skipped_count += 1
                continue
            location = _location_text(job.get("location"))
            salary_raw = _salary_text(job)
            records.append(
                SourceJobRecord(
                    source_job_key=job_id,
                    source_url=source_url,
                    apply_url=source_url,
                    title=title,
                    company=company,
                    location_text=location,
                    posted_at_raw=job.get("posted_at") or job.get("created_at"),
                    employment_type_raw=job.get("employment_type"),
                    description_raw=job.get("job_summary") or job.get("description"),
                    salary_raw=salary_raw,
                    tags_raw=[value for value in [job.get("industry"), job.get("experience_level")] if isinstance(value, str) and value.strip()],
                    raw_payload=job,
                )
            )
        if jobs and not records:
            raise ValueError("bossjob_ph_no_usable_jobs")
        return records, ParseStats(raw_seen_count=len(jobs), skipped_count=skipped_count)


def _company_name(job: dict, configured_company_name: str | None) -> str:
    company = job.get("company")
    if isinstance(company, dict):
        for key in ("company_name", "name"):
            value = str(company.get(key) or "").strip()
            if value:
                return value
    return configured_company_name or "Unknown"


def _location_text(value: object) -> str:
    if isinstance(value, dict):
        parts = [str(value.get(key) or "").strip() for key in ("city", "region", "country")]
        return ", ".join(part for part in parts if part)
    if isinstance(value, str):
        return value.strip()
    return ""


def _salary_text(job: dict) -> str | None:
    currency = job.get("currency") or "PHP"
    minimum = _format_amount(job.get("salary_min"))
    maximum = _format_amount(job.get("salary_max"))
    period = job.get("salary_period") or "month"
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


def _job_id_from_url(value: str) -> str:
    match = re.search(r"(\d{4,})", value)
    return match.group(1) if match else ""
