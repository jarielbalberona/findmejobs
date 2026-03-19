from __future__ import annotations

import json

from findmejobs.config.models import SmartRecruitersSourceConfig, SourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type


class SmartRecruitersAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, SmartRecruitersSourceConfig)
        return f"https://api.smartrecruiters.com/v1/companies/{config.company_identifier}/postings?limit={config.limit}"

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, SmartRecruitersSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        postings = payload.get("content")
        if not isinstance(postings, list):
            raise ValueError("invalid_smartrecruiters_payload")
        records: list[SourceJobRecord] = []
        for posting in postings:
            job_id = posting.get("id") or posting.get("ref")
            source_url = posting.get("ref") or posting.get("applyUrl")
            title = str(posting.get("name", "")).strip()
            if not job_id or not source_url or not title:
                continue
            location = posting.get("location") if isinstance(posting.get("location"), dict) else {}
            location_text = ", ".join(
                part for part in [location.get("city"), location.get("region"), location.get("country")] if part
            )
            department = posting.get("department") if isinstance(posting.get("department"), dict) else {}
            records.append(
                SourceJobRecord(
                    source_job_key=str(job_id),
                    source_url=source_url,
                    apply_url=posting.get("applyUrl") or source_url,
                    title=title,
                    company=_company_name(posting, config.company_name),
                    location_text=location_text,
                    posted_at_raw=posting.get("releasedDate"),
                    employment_type_raw=posting.get("typeOfEmployment"),
                    description_raw=posting.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text")
                    if isinstance(posting.get("jobAd"), dict)
                    else None,
                    tags_raw=[value for value in [department.get("label"), posting.get("typeOfEmployment")] if value],
                    raw_payload=posting,
                )
            )
        return records


def _company_name(posting: dict, configured_company_name: str | None) -> str:
    company = posting.get("company")
    if isinstance(company, dict):
        for key in ("name", "companyName"):
            value = str(company.get(key, "")).strip()
            if value:
                return value
    for key in ("companyName", "organization"):
        value = str(posting.get(key, "")).strip()
        if value:
            return value
    return configured_company_name or "Unknown"
