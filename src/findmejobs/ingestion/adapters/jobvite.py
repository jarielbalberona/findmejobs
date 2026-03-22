from __future__ import annotations

import json

from findmejobs.config.models import JobviteSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type


class JobviteAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, JobviteSourceConfig)
        return f"https://jobs.jobvite.com/api/jobs?c={config.company_code}"

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, JobviteSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = _extract_jobs(payload)
        if not isinstance(jobs, list):
            raise ValueError("invalid_jobvite_payload")

        records: list[SourceJobRecord] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("id") or job.get("jobId") or job.get("requisitionId") or "").strip()
            source_url = str(job.get("url") or job.get("jobUrl") or "").strip()
            title = str(job.get("title") or job.get("jobTitle") or "").strip()
            if not job_id or not source_url or not title:
                continue
            records.append(
                SourceJobRecord(
                    source_job_key=job_id,
                    source_url=source_url,
                    apply_url=str(job.get("applyUrl") or "").strip() or source_url,
                    title=title,
                    company=_company_name(job, config.company_name),
                    location_text=_location_text(job),
                    posted_at_raw=job.get("postedDate") or job.get("datePosted") or job.get("updated"),
                    employment_type_raw=str(job.get("employmentType") or job.get("type") or "").strip() or None,
                    description_raw=str(job.get("description") or job.get("jobDescription") or "").strip() or None,
                    tags_raw=_tags(job),
                    raw_payload=job,
                )
            )
        return records


def _extract_jobs(payload: object) -> list | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("jobs", "requisitions", "positions"):
            jobs = payload.get(key)
            if isinstance(jobs, list):
                return jobs
    return None


def _company_name(job: dict, configured_company_name: str | None) -> str:
    for key in ("company", "companyName", "organization"):
        value = str(job.get(key) or "").strip()
        if value:
            return value
    return configured_company_name or "Unknown"


def _location_text(job: dict) -> str:
    value = job.get("location")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = [str(value.get(key) or "").strip() for key in ("city", "state", "country")]
        return ", ".join(part for part in parts if part)
    for key in ("city", "state", "country"):
        text = str(job.get(key) or "").strip()
        if text:
            return text
    return ""


def _tags(job: dict) -> list[str]:
    values: list[str] = []
    for key in ("category", "department", "employmentType", "type"):
        value = str(job.get(key) or "").strip()
        if value:
            values.append(value)
    return values
