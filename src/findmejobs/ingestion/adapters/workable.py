from __future__ import annotations

import json

from findmejobs.config.models import SourceConfig, WorkableSourceConfig
from findmejobs.domain.source import FetchArtifact, SourceJobRecord
from findmejobs.ingestion.adapters.base import SourceAdapter, validate_config_type


class WorkableAdapter(SourceAdapter):
    def build_url(self, config: SourceConfig) -> str:
        validate_config_type(config, WorkableSourceConfig)
        details = "true" if config.include_details else "false"
        return f"https://www.workable.com/api/accounts/{config.account_subdomain}?details={details}"

    def parse(self, artifact: FetchArtifact, config: SourceConfig) -> list[SourceJobRecord]:
        validate_config_type(config, WorkableSourceConfig)
        payload = json.loads(artifact.body_bytes.decode("utf-8"))
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid_workable_payload")

        records: list[SourceJobRecord] = []
        for job in jobs:
            shortcode = job.get("shortcode") or job.get("code") or job.get("id")
            source_url = job.get("url") or job.get("shortlink") or job.get("application_url")
            title = str(job.get("title", "")).strip()
            if not shortcode or not source_url or not title:
                continue
            records.append(
                SourceJobRecord(
                    source_job_key=str(shortcode),
                    source_url=source_url,
                    apply_url=job.get("application_url") or source_url,
                    source_company_id=config.account_subdomain,
                    title=title,
                    company=_company_name(payload, job, config.company_name),
                    location_text=_location_text(job),
                    posted_at_raw=job.get("published_on") or job.get("created_at"),
                    employment_type_raw=_first_string(job, "employment_type", "type"),
                    salary_raw=_salary_text(job.get("salary")),
                    description_raw=job.get("description"),
                    tags_raw=_tags(job),
                    raw_payload=job,
                )
            )
        return records


def _company_name(payload: dict, job: dict, configured_company_name: str | None) -> str:
    for container in (job, payload):
        for key in ("company", "company_name", "name", "account_name"):
            value = str(container.get(key, "")).strip()
            if value:
                return value
    return configured_company_name or "Unknown"


def _location_text(job: dict) -> str:
    location = job.get("location")
    if isinstance(location, dict):
        location_str = str(location.get("location_str", "")).strip()
        if location_str:
            return location_str
        parts = [location.get("city"), location.get("region"), location.get("country")]
        text = ", ".join(str(part).strip() for part in parts if str(part).strip())
        if text:
            return text
    return str(job.get("location", "")).strip()


def _salary_text(raw_salary: object) -> str | None:
    if isinstance(raw_salary, str):
        return raw_salary.strip() or None
    if not isinstance(raw_salary, dict):
        return None
    salary_from = raw_salary.get("salary_from")
    salary_to = raw_salary.get("salary_to")
    currency = str(raw_salary.get("salary_currency", "")).strip().upper()
    if salary_from is None and salary_to is None:
        return None
    if salary_from is not None and salary_to is not None:
        return f"{currency} {salary_from} - {salary_to}".strip()
    value = salary_from if salary_from is not None else salary_to
    return f"{currency} {value}".strip()


def _tags(job: dict) -> list[str]:
    values: list[str] = []
    for key in ("department", "employment_type", "industry", "function", "experience", "education"):
        value = _first_string(job, key)
        if value:
            values.append(value)
    location = job.get("location")
    if isinstance(location, dict):
        workplace_type = str(location.get("workplace_type", "")).strip()
        if workplace_type:
            values.append(workplace_type)
        if location.get("telecommuting") is True:
            values.append("remote")
    return values


def _first_string(container: dict, *keys: str) -> str | None:
    for key in keys:
        value = str(container.get(key, "")).strip()
        if value:
            return value
    return None
