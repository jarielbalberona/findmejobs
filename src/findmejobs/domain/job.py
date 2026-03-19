from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CanonicalJob(BaseModel):
    source_job_id: str
    source_id: str
    source_job_key: str
    canonical_url: str | None = None
    company_name: str
    title: str
    location_text: str = ""
    location_type: str = "unknown"
    country_code: str | None = None
    city: str | None = None
    region: str | None = None
    seniority: str | None = None
    employment_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    description_text: str = ""
    tags: list[str] = Field(default_factory=list)
    posted_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    normalization_errors: list[str] = Field(default_factory=list)
