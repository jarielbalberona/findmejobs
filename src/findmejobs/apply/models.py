from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from findmejobs.application.models import ApplicationValidationReport
from findmejobs.utils.text import collapse_whitespace

HTML_TAG_RE = re.compile(r"<[^>]+>")


def _reject_markup(value: str, *, field_name: str) -> str:
    if HTML_TAG_RE.search(value):
        raise ValueError(f"{field_name} contains markup")
    return value


def _clean_text(value: object, *, field_name: str, max_length: int = 400) -> str:
    cleaned = collapse_whitespace(str(value or ""))
    _reject_markup(cleaned, field_name=field_name)
    if len(cleaned) > max_length:
        return cleaned[:max_length].rstrip()
    return cleaned


def _clean_text_list(value: object, *, field_name: str, max_length: int = 200) -> list[str]:
    if value in (None, ""):
        return []
    raw = [value] if isinstance(value, str) else list(value)  # type: ignore[arg-type]
    return [_clean_text(item, field_name=field_name, max_length=max_length) for item in raw if str(item).strip()]

ApplyMode = Literal["guided", "assisted"]
ApplySessionStatus = Literal[
    "opened",
    "in_progress",
    "awaiting_approval",
    "ready_to_resume",
    "awaiting_manual_submit",
    "cancelled",
]
ApprovalGateType = Literal[
    "final_submit",
    "unknown_question",
    "fallback_answer",
    "overwrite_conflict",
    "missing_file_upload",
    "low_confidence_parse",
]
ApprovalStatus = Literal["pending", "approved", "manual_only"]
FieldActionType = Literal[
    "autofill",
    "prefill_preserved",
    "skip_missing",
    "skip_risky",
    "overwrite_blocked",
    "upload_prepared_file",
]
FieldActionStatus = Literal["proposed", "filled", "preserved", "blocked", "skipped"]
CandidateValueType = Literal["text", "url", "textarea", "file"]


class ApplyArtifactState(BaseModel):
    packet_prepared: bool
    cover_letter_status: str
    answers_status: str
    readiness_state: str
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_validation(cls, report: ApplicationValidationReport) -> "ApplyArtifactState":
        return cls(
            packet_prepared=report.packet_prepared,
            cover_letter_status=report.cover_letter_status,
            answers_status=report.answers_status,
            readiness_state=report.readiness_state,
            validation_errors=report.errors,
            validation_warnings=report.warnings,
        )


class ApplyInputCandidate(BaseModel):
    key: str
    label: str
    value: str
    value_type: CandidateValueType = "text"
    source: Literal[
        "canonical_profile",
        "application_packet",
        "validated_cover_letter",
        "validated_answers",
        "session_override",
    ]
    confidence: Literal["high", "medium"] = "high"
    notes: str | None = None

    @field_validator("value")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        return _clean_text(value, field_name="apply_input_value", max_length=2400)

    @field_validator("key", "label", "source", "notes", mode="before")
    @classmethod
    def _normalize_scalars(cls, value: object) -> object:
        if value is None:
            return None
        return _clean_text(value, field_name="apply_input_scalar", max_length=200)


class ApplyFieldAction(BaseModel):
    action_id: str
    field_key: str
    label: str
    action_type: FieldActionType
    status: FieldActionStatus
    source: str
    proposed_value: str | None = None
    existing_value: str | None = None
    confidence: Literal["high", "medium", "low"] = "high"
    page: str | None = None
    notes: str | None = None

    @field_validator(
        "action_id",
        "field_key",
        "label",
        "source",
        "proposed_value",
        "existing_value",
        "page",
        "notes",
        mode="before",
    )
    @classmethod
    def _normalize_scalars(cls, value: object) -> object:
        if value is None:
            return None
        return _clean_text(value, field_name="apply_field_action", max_length=400)


class ApplyUnresolvedField(BaseModel):
    field_key: str
    label: str
    reason_code: str
    message: str
    required: bool = True
    page: str | None = None
    current_value: str | None = None
    canonical_value: str | None = None
    approval_action_id: str | None = None
    suggested_sources: list[str] = Field(default_factory=list)

    @field_validator(
        "field_key",
        "label",
        "reason_code",
        "message",
        "page",
        "current_value",
        "canonical_value",
        "approval_action_id",
        mode="before",
    )
    @classmethod
    def _normalize_scalars(cls, value: object) -> object:
        if value is None:
            return None
        return _clean_text(value, field_name="apply_unresolved_field", max_length=400)

    @field_validator("suggested_sources", mode="before")
    @classmethod
    def _normalize_sources(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="apply_unresolved_sources", max_length=120)


class ApplyApprovalGate(BaseModel):
    action_id: str
    gate_type: ApprovalGateType
    status: ApprovalStatus = "pending"
    title: str
    reason: str
    page: str | None = None
    field_key: str | None = None
    current_value: str | None = None
    proposed_value: str | None = None
    submit_available: bool = False
    approved_at: datetime | None = None

    @field_validator(
        "action_id",
        "title",
        "reason",
        "page",
        "field_key",
        "current_value",
        "proposed_value",
        mode="before",
    )
    @classmethod
    def _normalize_scalars(cls, value: object) -> object:
        if value is None:
            return None
        return _clean_text(value, field_name="apply_approval_gate", max_length=400)


class ApplyBrowserRequest(BaseModel):
    request_type: Literal["open_session", "resume_session"]
    request_version: str = "v1"
    job_id: str
    mode: ApplyMode
    browser_profile: str | None = None
    apply_url: str
    current_step: str | None = None
    current_page_url: str | None = None
    login_assumption: str = "manual_browser_session_already_authenticated_if_required"
    allow_multi_step: bool
    allow_submit: bool = False
    stop_before_irreversible_action: bool = True
    candidate_inputs: list[ApplyInputCandidate] = Field(default_factory=list)
    pending_approvals: list[ApplyApprovalGate] = Field(default_factory=list)
    unresolved_fields: list[ApplyUnresolvedField] = Field(default_factory=list)
    approved_actions: list[str] = Field(default_factory=list)
    session_overrides: dict[str, str] = Field(default_factory=dict)
    instructions: list[str] = Field(default_factory=list)


class ApplyBrowserResult(BaseModel):
    result_type: Literal["browser_progress"] = "browser_progress"
    result_version: str = "v1"
    event_id: str
    job_id: str
    step_id: str
    step_label: str
    page_url: str
    parse_confidence: float = Field(ge=0.0, le=1.0)
    safe_to_continue: bool = False
    submit_available: bool = False
    filled_fields: list[ApplyFieldAction] = Field(default_factory=list)
    unresolved_fields: list[ApplyUnresolvedField] = Field(default_factory=list)
    requested_approvals: list[ApplyApprovalGate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("event_id", "job_id", "step_id", "step_label", "page_url", mode="before")
    @classmethod
    def _normalize_scalars(cls, value: object) -> str:
        return _clean_text(value, field_name="apply_browser_result", max_length=400)

    @field_validator("notes", mode="before")
    @classmethod
    def _normalize_notes(cls, value: object) -> list[str]:
        return _clean_text_list(value, field_name="apply_browser_result_note", max_length=280)


class ApplySessionModel(BaseModel):
    session_version: str = "v1"
    job_id: str
    cluster_id: str
    mode: ApplyMode
    status: ApplySessionStatus
    browser_profile: str | None = None
    apply_url: str
    created_at: datetime
    updated_at: datetime
    current_step: str | None = None
    current_page_url: str | None = None
    parse_confidence: float | None = None
    submit_available: bool = False
    manual_submit_required: bool = True
    artifacts: ApplyArtifactState
    session_overrides: dict[str, str] = Field(default_factory=dict)
    pending_action_ids: list[str] = Field(default_factory=list)
    approved_action_ids: list[str] = Field(default_factory=list)
    consumed_event_ids: list[str] = Field(default_factory=list)
    last_browser_event_id: str | None = None
    last_browser_event_at: datetime | None = None
    last_resume_requested_at: datetime | None = None


class ApplySessionOpenResult(BaseModel):
    session: ApplySessionModel
    filled_fields: list[ApplyFieldAction] = Field(default_factory=list)
    unresolved_fields: list[ApplyUnresolvedField] = Field(default_factory=list)
    approvals_required: list[ApplyApprovalGate] = Field(default_factory=list)
    candidate_inputs: list[ApplyInputCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ApplySessionListRow(BaseModel):
    job_id: str
    mode: ApplyMode
    status: ApplySessionStatus
    company_name: str | None = None
    role_title: str | None = None
    current_step: str | None = None
    updated_at: datetime
    pending_approvals: int = 0
    unresolved_fields: int = 0
    submit_available: bool = False


class ApplySessionStatusReport(BaseModel):
    session: ApplySessionModel
    filled_fields: list[ApplyFieldAction] = Field(default_factory=list)
    unresolved_fields: list[ApplyUnresolvedField] = Field(default_factory=list)
    approvals_required: list[ApplyApprovalGate] = Field(default_factory=list)
    report_markdown: str | None = None
