from __future__ import annotations

import json

from findmejobs.config.models import GreenhouseSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type


class GreenhouseAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, GreenhouseSourceConfig)
        suffix = "true" if config.include_content else "false"
        return f"https://boards-api.greenhouse.io/v1/boards/{config.board_token}/jobs?content={suffix}"

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, GreenhouseSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = payload.get("jobs", [])
        records: list[SourceJobRecord] = []
        for job in jobs:
            job_id = job.get("id")
            absolute_url = job.get("absolute_url")
            title = str(job.get("title", "")).strip()
            if job_id is None or not absolute_url or not title:
                continue
            records.append(
                SourceJobRecord(
                    source_job_key=str(job_id),
                    source_url=absolute_url,
                    apply_url=absolute_url,
                    title=title,
                    company=_company_name(job, config.company_name),
                    location_text=(job.get("location") or {}).get("name", ""),
                    posted_at_raw=job.get("updated_at") or job.get("created_at"),
                    description_raw=job.get("content"),
                    tags_raw=[item["name"] for item in job.get("departments", []) if item.get("name")],
                    raw_payload=job,
                )
            )
        return records


def _company_name(job: dict, configured_company_name: str | None) -> str:
    for key in ("company", "companyName", "organization"):
        value = str(job.get(key, "")).strip()
        if value:
            return value
    return configured_company_name or "Unknown"
