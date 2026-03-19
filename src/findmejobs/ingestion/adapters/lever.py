from __future__ import annotations

import json

from findmejobs.config.models import LeverSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type


class LeverAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, LeverSourceConfig)
        return f"https://api.lever.co/v0/postings/{config.site}?mode=json"

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, LeverSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("invalid_lever_payload")
        records: list[SourceJobRecord] = []
        for job in payload:
            posting_id = job.get("id") or job.get("hostedUrl")
            source_url = job.get("hostedUrl") or job.get("applyUrl")
            title = str(job.get("text", "")).strip()
            if not posting_id or not source_url or not title:
                continue
            categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
            records.append(
                SourceJobRecord(
                    source_job_key=str(posting_id),
                    source_url=source_url,
                    apply_url=job.get("applyUrl") or source_url,
                    title=title,
                    company=_company_name(job, config.company_name),
                    location_text=str(categories.get("location", "")).strip(),
                    posted_at_raw=job.get("createdAt"),
                    employment_type_raw=categories.get("commitment"),
                    seniority_raw=categories.get("team"),
                    description_raw=job.get("descriptionPlain") or job.get("description"),
                    tags_raw=[value for value in categories.values() if isinstance(value, str) and value.strip()],
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
