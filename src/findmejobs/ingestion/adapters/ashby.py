from __future__ import annotations

import json

from findmejobs.config.models import AshbySourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type


class AshbyAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, AshbySourceConfig)
        return str(config.board_url)

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, AshbySourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = payload.get("jobs") or payload.get("jobPostings")
        if not isinstance(jobs, list):
            raise ValueError("invalid_ashby_payload")
        records: list[SourceJobRecord] = []
        for job in jobs:
            job_id = job.get("id") or job.get("jobPostingId") or job.get("slug")
            source_url = job.get("jobUrl") or job.get("absoluteUrl") or job.get("url")
            title = str(job.get("title", "") or job.get("name", "")).strip()
            if not job_id or not source_url or not title:
                continue
            location = job.get("location") if isinstance(job.get("location"), dict) else {}
            location_text = str(
                location.get("locationName")
                or location.get("name")
                or job.get("locationName")
                or ""
            ).strip()
            teams = job.get("teams") if isinstance(job.get("teams"), list) else []
            records.append(
                SourceJobRecord(
                    source_job_key=str(job_id),
                    source_url=source_url,
                    apply_url=job.get("applyUrl") or source_url,
                    title=title,
                    company=_company_name(job, config.company_name),
                    location_text=location_text,
                    posted_at_raw=job.get("publishedAt") or job.get("createdAt"),
                    employment_type_raw=job.get("employmentType"),
                    seniority_raw=job.get("seniority"),
                    description_raw=job.get("descriptionHtml") or job.get("description"),
                    tags_raw=[str(team.get("name")).strip() for team in teams if isinstance(team, dict) and team.get("name")],
                    raw_payload=job,
                )
            )
        return records


def _company_name(job: dict, configured_company_name: str | None) -> str:
    for key in ("companyName", "company", "organizationName"):
        value = job.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return configured_company_name or "Unknown"
