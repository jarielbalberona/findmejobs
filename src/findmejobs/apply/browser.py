from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Literal, Protocol

from findmejobs.apply.models import ApplyApprovalGate, ApplyBrowserRequest, ApplyBrowserResult, ApplyFieldAction, ApplyUnresolvedField
from findmejobs.utils.ids import new_id

LOW_CONFIDENCE_THRESHOLD = 0.7
NEXT_LABEL_RE = re.compile(r"\b(next|continue|review|preview)\b", re.IGNORECASE)
SUBMIT_LABEL_RE = re.compile(r"\b(submit|apply)\b", re.IGNORECASE)


@dataclass(slots=True)
class BrowserField:
    field_id: str
    label: str
    field_type: Literal["text", "email", "tel", "url", "textarea", "select", "checkbox", "radio", "file", "unknown"]
    value: str | None = None
    required: bool = False
    options: list[str] = field(default_factory=list)
    page: str | None = None


@dataclass(slots=True)
class BrowserStepSnapshot:
    step_id: str
    step_label: str
    page_url: str
    fields: list[BrowserField]
    parse_confidence: float
    next_action_label: str | None = None
    submit_visible: bool = False


class BrowserBackend(Protocol):
    def open(self, *, url: str, browser_profile: str | None = None, browser_profile_dir: Path | None = None) -> BrowserStepSnapshot: ...
    def fill(self, field: BrowserField, value: str) -> None: ...
    def upload(self, field: BrowserField, file_path: Path) -> None: ...
    def click_next(self, label: str | None = None) -> BrowserStepSnapshot: ...
    def close(self) -> None: ...


@dataclass(slots=True)
class _Analysis:
    category: str
    candidate_key: str | None = None
    candidate_value: str | None = None
    gate_type: str | None = None
    unresolved_reason: str | None = None
    unresolved_message: str | None = None


def build_browser_backend(backend_name: str = "playwright") -> BrowserBackend:
    if backend_name != "playwright":
        raise ValueError(f"unsupported_browser_backend:{backend_name}")
    from findmejobs.apply.playwright_backend import PlaywrightBrowserBackend

    return PlaywrightBrowserBackend()


class ApplyBrowserRunner:
    def __init__(self, backend: BrowserBackend) -> None:
        self.backend = backend

    def run(self, request: ApplyBrowserRequest, *, browser_profile_dir: Path | None = None) -> ApplyBrowserResult:
        filled_fields: list[ApplyFieldAction] = []
        unresolved_fields: list[ApplyUnresolvedField] = list(request.unresolved_fields)
        requested_approvals: list[ApplyApprovalGate] = list(request.pending_approvals)
        candidate_map = {item.key: item for item in request.candidate_inputs}
        notes: list[str] = []
        snapshot = self.backend.open(
            url=request.apply_url,
            browser_profile=request.browser_profile,
            browser_profile_dir=browser_profile_dir,
        )
        try:
            while True:
                step_filled, step_unresolved, step_approvals = self._process_snapshot(snapshot, candidate_map)
                filled_fields.extend(step_filled)
                unresolved_fields = self._merge_unresolved(unresolved_fields, step_unresolved)
                requested_approvals = self._merge_approvals(requested_approvals, step_approvals)
                submit_available = snapshot.submit_visible or bool(SUBMIT_LABEL_RE.search(snapshot.next_action_label or ""))
                if submit_available:
                    requested_approvals = self._merge_approvals(
                        requested_approvals,
                        [
                            ApplyApprovalGate(
                                action_id="final-submit-manual",
                                gate_type="final_submit",
                                status="manual_only",
                                title="Final submit is available but blocked",
                                reason="The browser runner must never click the final submit action.",
                                page=snapshot.step_label,
                                submit_available=True,
                            )
                        ],
                    )
                if snapshot.parse_confidence < LOW_CONFIDENCE_THRESHOLD:
                    requested_approvals = self._merge_approvals(
                        requested_approvals,
                        [
                            ApplyApprovalGate(
                                action_id="low-confidence-parse",
                                gate_type="low_confidence_parse",
                                title="Continue with low form parsing confidence",
                                reason=f"Browser parsing confidence dropped to {snapshot.parse_confidence:.2f}.",
                                page=snapshot.step_label,
                            )
                        ],
                    )
                safe_to_continue = (
                    request.allow_multi_step
                    and snapshot.parse_confidence >= LOW_CONFIDENCE_THRESHOLD
                    and not submit_available
                    and not any(g.status == "pending" for g in requested_approvals)
                    and bool(snapshot.next_action_label)
                )
                if request.mode == "guided":
                    notes.append("Guided mode stopped after safe field handling for the current page.")
                    safe_to_continue = False
                if not safe_to_continue:
                    return ApplyBrowserResult(
                        event_id=new_id(),
                        job_id=request.job_id,
                        step_id=snapshot.step_id,
                        step_label=snapshot.step_label,
                        page_url=snapshot.page_url,
                        parse_confidence=snapshot.parse_confidence,
                        safe_to_continue=False,
                        submit_available=submit_available,
                        filled_fields=filled_fields,
                        unresolved_fields=unresolved_fields,
                        requested_approvals=requested_approvals,
                        notes=notes,
                    )
                notes.append(f"Advanced safely via action: {snapshot.next_action_label}")
                snapshot = self.backend.click_next(snapshot.next_action_label)
        finally:
            self.backend.close()

    def _process_snapshot(
        self,
        snapshot: BrowserStepSnapshot,
        candidate_map: dict[str, object],
    ) -> tuple[list[ApplyFieldAction], list[ApplyUnresolvedField], list[ApplyApprovalGate]]:
        filled_fields: list[ApplyFieldAction] = []
        unresolved_fields: list[ApplyUnresolvedField] = []
        approvals: list[ApplyApprovalGate] = []
        for field in snapshot.fields:
            analysis = self._analyze_field(field, candidate_map)
            if analysis.category == "safe" and analysis.candidate_value is not None and analysis.candidate_key is not None:
                if field.value and field.value.strip() and field.value.strip() != analysis.candidate_value.strip():
                    approvals.append(
                        ApplyApprovalGate(
                            action_id=f"approve-overwrite-{field.field_id}",
                            gate_type="overwrite_conflict",
                            title=f"Overwrite conflicting value for {field.label}",
                            reason="The page is prefilled with a value that conflicts with app-owned data.",
                            page=field.page or snapshot.step_label,
                            field_key=analysis.candidate_key,
                            current_value=field.value,
                            proposed_value=analysis.candidate_value,
                        )
                    )
                    filled_fields.append(
                        ApplyFieldAction(
                            action_id=f"block-overwrite-{field.field_id}",
                            field_key=analysis.candidate_key,
                            label=field.label,
                            action_type="overwrite_blocked",
                            status="blocked",
                            source="page_prefill",
                            existing_value=field.value,
                            proposed_value=analysis.candidate_value,
                            page=field.page or snapshot.step_label,
                        )
                    )
                    continue
                if field.value and field.value.strip() == analysis.candidate_value.strip():
                    filled_fields.append(
                        ApplyFieldAction(
                            action_id=f"preserve-{field.field_id}",
                            field_key=analysis.candidate_key,
                            label=field.label,
                            action_type="prefill_preserved",
                            status="preserved",
                            source="page_prefill",
                            existing_value=field.value,
                            page=field.page or snapshot.step_label,
                        )
                    )
                    continue
                if field.field_type == "file":
                    file_path = Path(analysis.candidate_value)
                    if not file_path.exists():
                        approvals.append(
                            ApplyApprovalGate(
                                action_id=f"approve-upload-{field.field_id}",
                                gate_type="missing_file_upload",
                                title=f"Upload missing or unvalidated file for {field.label}",
                                reason="The file input requires a validated file path that is not available.",
                                page=field.page or snapshot.step_label,
                                field_key=analysis.candidate_key,
                            )
                        )
                        unresolved_fields.append(
                            ApplyUnresolvedField(
                                field_key=analysis.candidate_key,
                                label=field.label,
                                reason_code="missing_file_upload",
                                message="Validated file artifact is missing for this upload field.",
                                page=field.page or snapshot.step_label,
                                approval_action_id=f"approve-upload-{field.field_id}",
                            )
                        )
                        continue
                    self.backend.upload(field, file_path)
                    filled_fields.append(
                        ApplyFieldAction(
                            action_id=f"upload-{field.field_id}",
                            field_key=analysis.candidate_key,
                            label=field.label,
                            action_type="upload_prepared_file",
                            status="filled",
                            source="canonical_profile",
                            proposed_value=str(file_path),
                            page=field.page or snapshot.step_label,
                        )
                    )
                    continue
                self.backend.fill(field, analysis.candidate_value)
                filled_fields.append(
                    ApplyFieldAction(
                        action_id=f"fill-{field.field_id}",
                        field_key=analysis.candidate_key,
                        label=field.label,
                        action_type="autofill",
                        status="filled",
                        source="candidate_input",
                        proposed_value=analysis.candidate_value,
                        page=field.page or snapshot.step_label,
                    )
                )
                continue
            if analysis.category == "unknown":
                action_id = f"approve-unknown-{field.field_id}"
                approvals.append(
                    ApplyApprovalGate(
                        action_id=action_id,
                        gate_type="unknown_question",
                        title=f"Unknown question for {field.label}",
                        reason="The field is not covered by safe app-owned inputs.",
                        page=field.page or snapshot.step_label,
                    )
                )
                unresolved_fields.append(
                    ApplyUnresolvedField(
                        field_key=field.field_id,
                        label=field.label,
                        reason_code=analysis.unresolved_reason or "unknown_question",
                        message=analysis.unresolved_message or "Unknown question requires operator review.",
                        page=field.page or snapshot.step_label,
                        approval_action_id=action_id,
                    )
                )
                continue
            if analysis.category == "sensitive":
                unresolved_fields.append(
                    ApplyUnresolvedField(
                        field_key=analysis.candidate_key or field.field_id,
                        label=field.label,
                        reason_code=analysis.unresolved_reason or "missing_sensitive_input",
                        message=analysis.unresolved_message or "Sensitive operator-owned input is missing.",
                        page=field.page or snapshot.step_label,
                    )
                )
        return filled_fields, unresolved_fields, approvals

    def _analyze_field(self, field: BrowserField, candidate_map: dict[str, object]) -> _Analysis:
        normalized = collapse_label(field.label)
        if any(token in normalized for token in ("full name", "name")) and "company" not in normalized:
            return self._candidate("full_name", candidate_map)
        if "email" in normalized:
            return self._candidate("email", candidate_map)
        if any(token in normalized for token in ("phone", "mobile", "contact number")):
            return self._candidate("phone", candidate_map)
        if "linkedin" in normalized:
            return self._candidate("linkedin_url", candidate_map)
        if "github" in normalized:
            return self._candidate("github_url", candidate_map)
        if any(token in normalized for token in ("portfolio", "website", "personal site")):
            return self._candidate("portfolio_url", candidate_map)
        if any(token in normalized for token in ("location", "city", "current location")):
            return self._candidate("location_text", candidate_map)
        if any(token in normalized for token in ("cover letter",)) and field.field_type in {"textarea", "text"}:
            return self._candidate("cover_letter_text", candidate_map)
        if any(token in normalized for token in ("resume", "cv")) and field.field_type == "file":
            return self._candidate("resume_file", candidate_map)
        if "why are you a fit" in normalized or "why should we hire you" in normalized:
            return self._candidate("answer:fit", candidate_map)
        if "why do you want" in normalized or "why are you interested" in normalized:
            return self._candidate("answer:motivation", candidate_map)
        if any(token in normalized for token in ("salary", "compensation", "expected pay")):
            return _Analysis(
                category="sensitive",
                candidate_key="salary_expectation",
                unresolved_reason="missing_salary_expectation",
                unresolved_message="Salary expectation must come from explicit operator-owned data.",
            )
        if "notice period" in normalized:
            return _Analysis(
                category="sensitive",
                candidate_key="notice_period",
                unresolved_reason="missing_notice_period",
                unresolved_message="Notice period must come from explicit operator-owned data.",
            )
        if any(token in normalized for token in ("work authorization", "authorized to work", "visa")):
            return _Analysis(
                category="sensitive",
                candidate_key="work_authorization",
                unresolved_reason="missing_work_authorization",
                unresolved_message="Work authorization must come from explicit operator-owned data.",
            )
        if "relocat" in normalized:
            return _Analysis(
                category="sensitive",
                candidate_key="relocation_preference",
                unresolved_reason="missing_relocation_preference",
                unresolved_message="Relocation preference must come from explicit operator-owned data.",
            )
        if any(token in normalized for token in ("timezone", "time zone", "hours", "availability")):
            return _Analysis(
                category="sensitive",
                candidate_key="work_hours",
                unresolved_reason="missing_timezone_availability",
                unresolved_message="Timezone/work-hours availability must come from explicit operator-owned data.",
            )
        if field.field_type in {"textarea", "text", "select", "radio", "checkbox"}:
            return _Analysis(
                category="unknown",
                unresolved_reason="unknown_question",
                unresolved_message="Unknown or low-confidence field requires operator review.",
            )
        return _Analysis(category="ignore")

    def _candidate(self, key: str, candidate_map: dict[str, object]) -> _Analysis:
        candidate = candidate_map.get(key)
        if candidate is None:
            return _Analysis(category="sensitive", candidate_key=key, unresolved_message="Required app-owned input is missing.")
        return _Analysis(category="safe", candidate_key=key, candidate_value=getattr(candidate, "value"))

    def _merge_unresolved(self, existing: list[ApplyUnresolvedField], updates: list[ApplyUnresolvedField]) -> list[ApplyUnresolvedField]:
        merged = {(item.field_key, item.reason_code): item for item in existing}
        for item in updates:
            merged[(item.field_key, item.reason_code)] = item
        return sorted(merged.values(), key=lambda item: (item.field_key, item.reason_code))

    def _merge_approvals(self, existing: list[ApplyApprovalGate], updates: list[ApplyApprovalGate]) -> list[ApplyApprovalGate]:
        merged = {item.action_id: item for item in existing}
        for item in updates:
            current = merged.get(item.action_id)
            if current is not None and current.status == "approved":
                continue
            merged[item.action_id] = item
        return sorted(merged.values(), key=lambda item: item.action_id)


def collapse_label(value: str) -> str:
    return " ".join(value.casefold().strip().split())
