from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from findmejobs.utils.text import collapse_whitespace

HTML_TAG_RE = re.compile(r"<[^>]+>")
MAX_APPLICATION_PACKET_BYTES = 24 * 1024
MAX_DRAFT_REQUEST_BYTES = 28 * 1024
MAX_QUESTION_LENGTH = 400
MAX_EXCERPT_LENGTH = 2400
MAX_PACKET_LIST_ITEM_LENGTH = 280
MAX_DRAFT_BODY_LENGTH = 2400
MAX_ANSWER_LENGTH = 480
MAX_MISSING_INPUT_KEY_LENGTH = 80


def _reject_markup(value: str, *, field_name: str) -> str:
    if HTML_TAG_RE.search(value):
        raise ValueError(f"{field_name} contains markup")
    return value


def _clean_text(value: str, *, field_name: str, max_length: int | None = None) -> str:
    cleaned = collapse_whitespace(value)
    _reject_markup(cleaned, field_name=field_name)
    if max_length is not None and len(cleaned) > max_length:
        return cleaned[:max_length].rstrip()
    return cleaned


def _clean_text_list(
    value: object,
    *,
    field_name: str,
    max_length: int = MAX_PACKET_LIST_ITEM_LENGTH,
) -> list[str]:
    if value in (None, ""):
        return []
    raw = [value] if isinstance(value, str) else list(value)  # type: ignore[arg-type]
    return [
        _clean_text(str(item), field_name=field_name, max_length=max_length)
        for item in raw
        if str(item).strip()
    ]


def _clean_multiline_text(value: object, *, field_name: str, max_length: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [collapse_whitespace(line) if line.strip() else "" for line in text.split("\n")]
    cleaned = "\n".join(lines).strip()
    _reject_markup(cleaned, field_name=field_name)
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length")
    return cleaned


class ApplicationQuestionModel(BaseModel):
    question_id: str
    prompt: str
    source: str = "provided"
    response_type: str | None = None
    required: bool = False
    normalized_key: str | None = None
    options: list[str] = Field(default_factory=list)

    @field_validator("question_id", "prompt", "source", "response_type", "normalized_key", mode="before")
    @classmethod
    def _normalize_scalar_fields(cls, value: object) -> object:
        if value is None:
            return None
        return _clean_text(str(value), field_name="application_question", max_length=MAX_QUESTION_LENGTH)

    @field_validator("options", mode="before")
    @classmethod
    def _normalize_options(cls, value: object) -> list[str]:
        if value in (None, ""):
            return []
        raw = [value] if isinstance(value, str) else list(value)  # type: ignore[arg-type]
        return [_clean_text(str(item), field_name="application_question_option", max_length=80) for item in raw if str(item).strip()]


class ApplicationSourceSummary(BaseModel):
    source_id: str
    source_name: str
    source_kind: str
    source_job_key: str
    source_url: str
    apply_url: str | None = None
    trust_weight: float
    priority: int

    @model_validator(mode="after")
    def reject_markup(self) -> "ApplicationSourceSummary":
        for key, value in self.model_dump().items():
            if isinstance(value, str):
                _reject_markup(value, field_name=key)
        return self


class ApplicationCanonicalJobSummary(BaseModel):
    company_name: str
    role_title: str
    location_text: str
    location_type: str
    country_code: str | None = None
    city: str | None = None
    region: str | None = None
    seniority: str | None = None
    employment_type: str | None = None
    salary_summary: str | None = None
    posted_at: datetime | None = None
    canonical_url: str | None = None
    description_excerpt: str
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> list[str]:
        if value in (None, ""):
            return []
        raw = [value] if isinstance(value, str) else list(value)  # type: ignore[arg-type]
        return [_clean_text(str(item), field_name="tag", max_length=50) for item in raw if str(item).strip()]

    @model_validator(mode="after")
    def reject_markup(self) -> "ApplicationCanonicalJobSummary":
        for key, value in self.model_dump().items():
            if isinstance(value, str):
                _reject_markup(value, field_name=key)
        return self


class ApplicationScoreSummary(BaseModel):
    total: float
    breakdown: dict[str, float] = Field(default_factory=dict)
    breakdown_summary: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)

    @field_validator("breakdown_summary", "matched_signals", mode="before")
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="score_list", max_length=120)


class ApplicationReviewSummary(BaseModel):
    packet_version: str
    review_status: str
    description_excerpt: str
    matched_signals: list[str] = Field(default_factory=list)
    decision: str | None = None
    reasons: list[str] = Field(default_factory=list)
    draft_summary: str | None = None

    @model_validator(mode="after")
    def reject_markup(self) -> "ApplicationReviewSummary":
        for key, value in self.model_dump().items():
            if isinstance(value, str):
                _reject_markup(value, field_name=key)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        _reject_markup(item, field_name=key)
        return self


class ApplicationMatchedProfileSummary(BaseModel):
    profile_version: str
    full_name: str | None = None
    email: str | None = None
    location_text: str | None = None
    target_titles: list[str] = Field(default_factory=list)
    matched_required_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    matched_preferred_skills: list[str] = Field(default_factory=list)
    summary_lines: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_markup(self) -> "ApplicationMatchedProfileSummary":
        for key, value in self.model_dump().items():
            if isinstance(value, str):
                _reject_markup(value, field_name=key)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        _reject_markup(item, field_name=key)
        return self


class ApplicationMissingInput(BaseModel):
    key: str
    reason: str
    questions: list[str] = Field(default_factory=list)
    required_for: list[str] = Field(default_factory=list)

    @field_validator("key", mode="before")
    @classmethod
    def _normalize_key(cls, value: object) -> str:
        return _clean_text(str(value), field_name="missing_input_key", max_length=MAX_MISSING_INPUT_KEY_LENGTH)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str:
        return _clean_text(str(value), field_name="missing_input_reason", max_length=240)

    @field_validator("questions", "required_for", mode="before")
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="missing_input_list", max_length=MAX_QUESTION_LENGTH)

    @model_validator(mode="after")
    def reject_markup(self) -> "ApplicationMissingInput":
        for key, value in self.model_dump().items():
            if isinstance(value, str):
                _reject_markup(value, field_name=key)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        _reject_markup(item, field_name=key)
        return self


class ApplicationPacketModel(BaseModel):
    packet_version: str = "v1"
    job_id: str
    cluster_id: str
    company_name: str
    role_title: str
    source: ApplicationSourceSummary
    canonical_job: ApplicationCanonicalJobSummary
    score: ApplicationScoreSummary
    review_summary: ApplicationReviewSummary
    matched_profile: ApplicationMatchedProfileSummary
    relevant_strengths: list[str] = Field(default_factory=list)
    detected_gaps: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    application_questions: list[ApplicationQuestionModel] = Field(default_factory=list)
    safe_context: list[str] = Field(default_factory=list)

    @field_validator("relevant_strengths", "detected_gaps", "unknowns", "safe_context", mode="before")
    @classmethod
    def _normalize_packet_lists(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="application_packet_list", max_length=MAX_PACKET_LIST_ITEM_LENGTH)

    @model_validator(mode="after")
    def validate_budget(self) -> "ApplicationPacketModel":
        if len(self.model_dump_json().encode("utf-8")) > MAX_APPLICATION_PACKET_BYTES:
            raise ValueError("application packet exceeds maximum size")
        return self


class ApplicationValidationReport(BaseModel):
    job_id: str
    eligible: bool
    complete: bool = False
    packet_prepared: bool = False
    packet_sha256: str | None = None
    cover_letter_status: str = "missing"
    answers_status: str = "missing"
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CoverLetterDraftModel(BaseModel):
    draft_version: str = "v1"
    job_id: str
    company_name: str
    role_title: str
    origin: str
    prompt_version: str
    body_markdown: str
    missing_inputs: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("job_id", "company_name", "role_title", "origin", "prompt_version", mode="before")
    @classmethod
    def _normalize_scalars(cls, value: object) -> str:
        return _clean_text(str(value), field_name="cover_letter_scalar", max_length=120)

    @field_validator("body_markdown", mode="before")
    @classmethod
    def _normalize_body(cls, value: object) -> str:
        return _clean_multiline_text(value, field_name="body_markdown", max_length=MAX_DRAFT_BODY_LENGTH) + "\n"

    @field_validator("missing_inputs", mode="before")
    @classmethod
    def _normalize_missing_inputs(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="cover_letter_missing_inputs", max_length=MAX_MISSING_INPUT_KEY_LENGTH)


class ApplicationAnswerDraftModel(BaseModel):
    question_id: str
    question: str
    normalized_key: str | None = None
    answer: str
    needs_user_input: bool = False
    missing_inputs: list[str] = Field(default_factory=list)

    @field_validator("question_id", mode="before")
    @classmethod
    def _normalize_question_id(cls, value: object) -> str:
        return _clean_text(str(value), field_name="question_id", max_length=120)

    @field_validator("question", mode="before")
    @classmethod
    def _normalize_question(cls, value: object) -> str:
        return _clean_text(str(value), field_name="question", max_length=MAX_QUESTION_LENGTH)

    @field_validator("normalized_key", mode="before")
    @classmethod
    def _normalize_key(cls, value: object) -> object:
        if value is None:
            return None
        return _clean_text(str(value), field_name="normalized_key", max_length=80)

    @field_validator("answer", mode="before")
    @classmethod
    def _normalize_answer(cls, value: object) -> str:
        return _clean_text(str(value), field_name="answer", max_length=MAX_ANSWER_LENGTH)

    @field_validator("missing_inputs", mode="before")
    @classmethod
    def _normalize_missing_inputs(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="answer_missing_inputs", max_length=MAX_MISSING_INPUT_KEY_LENGTH)

    @model_validator(mode="after")
    def validate_missing_input_flags(self) -> "ApplicationAnswerDraftModel":
        if self.needs_user_input and not self.missing_inputs:
            raise ValueError(f"answer_missing_input_flag_incomplete:{self.question_id}")
        return self


class AnswerDraftSetModel(BaseModel):
    draft_version: str = "v1"
    job_id: str
    origin: str
    prompt_version: str
    answers: list[ApplicationAnswerDraftModel] = Field(default_factory=list)
    missing_inputs: list[ApplicationMissingInput] = Field(default_factory=list)
    created_at: datetime

    @field_validator("job_id", "origin", "prompt_version", mode="before")
    @classmethod
    def _normalize_scalars(cls, value: object) -> str:
        return _clean_text(str(value), field_name="answer_set_scalar", max_length=120)


class CoverLetterDraftRequestModel(BaseModel):
    draft_type: str = "cover_letter"
    prompt_version: str
    instructions: str
    application_packet: ApplicationPacketModel
    output_schema: dict[str, object]

    @model_validator(mode="after")
    def validate_budget(self) -> "CoverLetterDraftRequestModel":
        if len(self.model_dump_json().encode("utf-8")) > MAX_DRAFT_REQUEST_BYTES:
            raise ValueError("cover letter request exceeds maximum size")
        return self


class CoverLetterDraftResultModel(BaseModel):
    draft_type: str = "cover_letter"
    prompt_version: str
    body_markdown: str
    missing_inputs: list[str] = Field(default_factory=list)
    raw_response: dict = Field(default_factory=dict)

    @field_validator("draft_type", "prompt_version", mode="before")
    @classmethod
    def _normalize_scalars(cls, value: object) -> str:
        return _clean_text(str(value), field_name="cover_letter_result_scalar", max_length=120)

    @field_validator("body_markdown", mode="before")
    @classmethod
    def _normalize_body(cls, value: object) -> str:
        return _clean_multiline_text(value, field_name="cover_letter_result_body", max_length=MAX_DRAFT_BODY_LENGTH)

    @field_validator("missing_inputs", mode="before")
    @classmethod
    def _normalize_missing_inputs(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="cover_letter_result_missing_inputs", max_length=MAX_MISSING_INPUT_KEY_LENGTH)


class AnswerDraftRequestModel(BaseModel):
    draft_type: str = "answers"
    prompt_version: str
    instructions: str
    application_packet: ApplicationPacketModel
    questions: list[ApplicationQuestionModel]
    output_schema: dict[str, object]

    @model_validator(mode="after")
    def validate_budget(self) -> "AnswerDraftRequestModel":
        if len(self.model_dump_json().encode("utf-8")) > MAX_DRAFT_REQUEST_BYTES:
            raise ValueError("answers request exceeds maximum size")
        return self


class AnswerDraftResultModel(BaseModel):
    draft_type: str = "answers"
    prompt_version: str
    answers: list[ApplicationAnswerDraftModel] = Field(default_factory=list)
    missing_inputs: list[ApplicationMissingInput] = Field(default_factory=list)
    raw_response: dict = Field(default_factory=dict)

    @field_validator("draft_type", "prompt_version", mode="before")
    @classmethod
    def _normalize_scalars(cls, value: object) -> str:
        return _clean_text(str(value), field_name="answer_result_scalar", max_length=120)


class ApplicationArtifactMetadata(BaseModel):
    artifact_type: Literal["cover_letter", "answers"]
    job_id: str
    origin: str
    prompt_version: str
    packet_sha256: str
    created_at: datetime
    missing_input_keys: list[str] = Field(default_factory=list)
    answer_count: int | None = None

    @field_validator("job_id", "origin", "prompt_version", "packet_sha256", mode="before")
    @classmethod
    def _normalize_scalars(cls, value: object) -> str:
        return _clean_text(str(value), field_name="artifact_metadata_scalar", max_length=120)

    @field_validator("missing_input_keys", mode="before")
    @classmethod
    def _normalize_missing_input_keys(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="artifact_metadata_missing_input_keys", max_length=MAX_MISSING_INPUT_KEY_LENGTH)
