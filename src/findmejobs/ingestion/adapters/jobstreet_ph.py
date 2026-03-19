from __future__ import annotations

import json
import re

from findmejobs.config.models import JobStreetPHSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import ParseStats, SourceAdapter, validate_config_type
from findmejobs.utils.urls import canonicalize_url


class JobStreetPHAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, JobStreetPHSourceConfig)
        return str(config.board_url)

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        return self.parse_with_stats(artifact, config)[0]

    def parse_with_stats(self, artifact: FetchArtifact, config: SourceConfig) -> tuple[list[SourceJobRecord], ParseStats]:
        validate_config_type(config, JobStreetPHSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = payload.get("data", {}).get("jobs") if isinstance(payload.get("data"), dict) else payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid_jobstreet_ph_payload")
        records: list[SourceJobRecord] = []
        skipped_count = 0
        for job in jobs:
            job_id = str(job.get("jobId") or job.get("id") or _job_id_from_url(job.get("jobUrl") or job.get("url") or ""))
            source_url = canonicalize_url(job.get("jobUrl") or job.get("url"))
            title = str(job.get("jobTitle") or job.get("title") or "").strip()
            company = str(job.get("companyName") or config.company_name or "Unknown").strip()
            if not job_id or not source_url or not title:
                skipped_count += 1
                continue
            location = str(job.get("location") or job.get("locationName") or "").strip()
            department = str(job.get("specialization") or job.get("department") or "").strip()
            salary_raw = _salary_text(job.get("salary"))
            description = job.get("jobDescription") or job.get("description") or job.get("teaser")
            tags = [value for value in [department, job.get("workType"), job.get("employmentType")] if isinstance(value, str) and value.strip()]
            records.append(
                SourceJobRecord(
                    source_job_key=job_id,
                    source_url=source_url,
                    apply_url=source_url,
                    title=title,
                    company=company,
                    location_text=location,
                    posted_at_raw=job.get("listingDate") or job.get("postedAt"),
                    employment_type_raw=job.get("employmentType") or job.get("workType"),
                    salary_raw=salary_raw,
                    description_raw=description,
                    tags_raw=tags,
                    raw_payload=job,
                )
            )
        if jobs and not records:
            raise ValueError("jobstreet_ph_no_usable_jobs")
        return records, ParseStats(raw_seen_count=len(jobs), skipped_count=skipped_count)


def _salary_text(value: object) -> str | None:
    if isinstance(value, dict):
        min_value = value.get("min") or value.get("minimum")
        max_value = value.get("max") or value.get("maximum")
        currency = value.get("currency") or "PHP"
        period = value.get("period") or "month"
        if min_value or max_value:
            left = _format_amount(min_value)
            right = _format_amount(max_value)
            joined = left if right is None or left == right else f"{left} - {right}"
            return f"{currency} {joined} / {period}" if joined else None
    if isinstance(value, str) and value.strip():
        return value.strip()
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
