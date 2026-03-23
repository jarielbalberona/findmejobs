from __future__ import annotations

import json
import re

from findmejobs.config.models import FounditPHSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import ParseStats, SourceAdapter, validate_config_type
from findmejobs.utils.urls import canonicalize_url


class FounditPHAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, FounditPHSourceConfig)
        return str(config.board_url)

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        return self.parse_with_stats(artifact, config)[0]

    def parse_with_stats(self, artifact: FetchArtifact, config: SourceConfig) -> tuple[list[SourceJobRecord], ParseStats]:
        validate_config_type(config, FounditPHSourceConfig)
        payload = _load_payload(artifact)
        jobs = payload.get("jobs") or payload.get("data", {}).get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid_foundit_ph_payload")
        records: list[SourceJobRecord] = []
        skipped_count = 0
        for job in jobs:
            source_url = canonicalize_url(job.get("jobUrl") or job.get("url"))
            job_id = str(job.get("jobId") or job.get("id") or _job_id_from_url(source_url or ""))
            title = str(job.get("title") or "").strip()
            company = str(job.get("companyName") or config.company_name or "Unknown").strip()
            if not source_url or not job_id or not title:
                skipped_count += 1
                continue
            records.append(
                SourceJobRecord(
                    source_job_key=job_id,
                    source_url=source_url,
                    apply_url=source_url,
                    title=title,
                    company=company,
                    location_text=_location_text(job.get("locations")),
                    posted_at_raw=job.get("postedDate") or job.get("publishedAt"),
                    employment_type_raw=job.get("employmentType"),
                    description_raw=job.get("jobDescription") or job.get("summary"),
                    salary_raw=str(job.get("salaryText") or "").strip() or None,
                    tags_raw=_list_text(job.get("functions")) + _list_text(job.get("skills")),
                    raw_payload=job,
                )
            )
        if jobs and not records:
            raise ValueError("foundit_ph_no_usable_jobs")
        return records, ParseStats(raw_seen_count=len(jobs), skipped_count=skipped_count)


def _location_text(value: object) -> str:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " / ".join(parts)
    if isinstance(value, str):
        return value.strip()
    return ""


def _list_text(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _job_id_from_url(value: str) -> str:
    match = re.search(r"(\d{4,})", value)
    return match.group(1) if match else ""


def _load_payload(artifact: FetchArtifact) -> dict:
    try:
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_foundit_ph_payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_foundit_ph_payload")
    return payload
