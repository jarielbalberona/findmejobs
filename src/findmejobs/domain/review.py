from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, Field, model_validator

HTML_TAG_RE = re.compile(r"<[^>]+>")


class ReviewPacketModel(BaseModel):
    packet_id: str
    packet_version: str
    cluster_id: str
    company_name: str
    title: str
    location: str
    employment_type: str | None = None
    seniority: str | None = None
    salary_summary: str | None = None
    posted_at: datetime | None = None
    canonical_url: str | None = None
    score_total: float
    score_breakdown: dict[str, float]
    matched_signals: list[str] = Field(default_factory=list)
    description_excerpt: str
    review_instructions_version: str = "slice1"

    @model_validator(mode="after")
    def reject_markup(self) -> "ReviewPacketModel":
        for key, value in self.model_dump().items():
            if isinstance(value, str) and HTML_TAG_RE.search(value):
                raise ValueError(f"review packet field {key} contains markup")
        return self


class ReviewResultModel(BaseModel):
    packet_id: str
    provider_review_id: str | None = None
    decision: str
    confidence_label: str | None = None
    reasons: list[str] = Field(default_factory=list)
    draft_summary: str | None = None
    draft_actions: list[str] = Field(default_factory=list)
    reviewed_at: datetime
    raw_response: dict
