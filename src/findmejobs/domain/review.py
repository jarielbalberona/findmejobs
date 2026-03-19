from __future__ import annotations

from datetime import datetime
import re
import json

from pydantic import BaseModel, Field, field_validator, model_validator

HTML_TAG_RE = re.compile(r"<[^>]+>")
MAX_RAW_RESPONSE_BYTES = 16 * 1024
MAX_RAW_RESPONSE_KEYS = 64


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

    @field_validator("raw_response")
    @classmethod
    def validate_raw_response(cls, value: dict) -> dict:
        if len(value) > MAX_RAW_RESPONSE_KEYS:
            raise ValueError("raw_response_too_many_keys")
        encoded = json.dumps(value, ensure_ascii=True)
        if len(encoded.encode("utf-8")) > MAX_RAW_RESPONSE_BYTES:
            raise ValueError("raw_response_too_large")
        return value
