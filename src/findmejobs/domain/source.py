from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class FetchArtifact(BaseModel):
    fetched_url: str
    final_url: str
    status_code: int
    content_type: str | None
    headers: dict[str, str]
    fetched_at: datetime
    body_bytes: bytes
    sha256: str
    storage_path: str


class SourceJobRecord(BaseModel):
    source_job_key: str
    source_url: str
    apply_url: str | None = None
    title: str
    company: str
    location_text: str = ""
    posted_at_raw: str | None = None
    employment_type_raw: str | None = None
    seniority_raw: str | None = None
    salary_raw: str | None = None
    description_raw: str | None = None
    tags_raw: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
