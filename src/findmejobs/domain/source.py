from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

PREDICTABLE_ATS_KINDS = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters", "workable", "breezy_hr", "jobvite"})
PH_BOARD_KINDS = frozenset({"jobstreet_ph", "kalibrr", "bossjob_ph", "foundit_ph"})
DIRECT_PAGE_KINDS = frozenset({"direct_page"})
DISCOVERY_KINDS = frozenset({"rss"})
TransportKind = Literal["api_json", "feed_xml", "html_scrape"]


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
    source_company_id: str | None = None
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


def source_family_for_kind(kind: str) -> str:
    if kind in PREDICTABLE_ATS_KINDS:
        return "predictable_ats"
    if kind in PH_BOARD_KINDS:
        return "ph_board"
    if kind in DIRECT_PAGE_KINDS:
        return "direct_page"
    if kind in DISCOVERY_KINDS:
        return "feed"
    return "unknown"


def transport_for_kind(kind: str) -> TransportKind:
    if kind in PREDICTABLE_ATS_KINDS or kind in PH_BOARD_KINDS:
        return "api_json"
    if kind in DIRECT_PAGE_KINDS:
        return "html_scrape"
    if kind in DISCOVERY_KINDS:
        return "feed_xml"
    return "api_json"
