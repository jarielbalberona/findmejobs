from __future__ import annotations

import json

from findmejobs.config.models import BreezyHRSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type


class BreezyHRAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, BreezyHRSourceConfig)
        return f"https://{config.company_subdomain}.breezy.hr/json"

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, BreezyHRSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = _extract_jobs(payload)
        if not isinstance(jobs, list):
            raise ValueError("invalid_breezy_hr_payload")

        records: list[SourceJobRecord] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("id") or job.get("_id") or "").strip()
            source_url = str(job.get("url") or job.get("public_url") or "").strip()
            title = str(job.get("name") or job.get("title") or "").strip()
            if not job_id or not source_url or not title:
                continue
            location = _location_text(job.get("location"))
            records.append(
                SourceJobRecord(
                    source_job_key=job_id,
                    source_url=source_url,
                    apply_url=source_url,
                    title=title,
                    company=_company_name(job, config.company_name),
                    location_text=location,
                    posted_at_raw=job.get("date") or job.get("created_at") or job.get("updated_at"),
                    employment_type_raw=str(job.get("type") or "").strip() or None,
                    description_raw=str(job.get("description") or "").strip() or None,
                    tags_raw=_tags(job),
                    raw_payload=job,
                )
            )
        return records


def _extract_jobs(payload: object) -> list | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            return jobs
    return None


def _company_name(job: dict, configured_company_name: str | None) -> str:
    for key in ("company", "companyName", "organization"):
        value = str(job.get(key) or "").strip()
        if value:
            return value
    return configured_company_name or "Unknown"


def _location_text(value: object) -> str:
    if isinstance(value, dict):
        parts = []
        for key in ("name", "city", "state", "country"):
            text = str(value.get(key) or "").strip()
            if text:
                parts.append(text)
        return ", ".join(parts)
    if isinstance(value, str):
        return value.strip()
    return ""


def _tags(job: dict) -> list[str]:
    tags: list[str] = []
    for key in ("department", "team", "category", "type"):
        value = str(job.get(key) or "").strip()
        if value:
            tags.append(value)
    return tags
