#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        warnings.append(f"read_json_failed:{path.name}:{exc}")
        return None


def _read_text(path: Path, warnings: list[str]) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"read_text_failed:{path.name}:{exc}")
        return None


def _from_packet(job_id: str, application_state_root: Path) -> tuple[str | None, str | None]:
    packet_path = application_state_root / job_id / "application_packet.json"
    if not packet_path.exists():
        return None, None
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, None
    return packet.get("company_name"), packet.get("role_title")


def _session_entry(session_root: Path, application_state_root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    job_id = session_root.name

    session = _read_json(session_root / "session.json", warnings)
    if not isinstance(session, dict):
        warnings.append("missing_or_invalid:session.json")
        return None, warnings

    summary = _read_json(session_root / "summary.json", warnings)
    filled_fields = _read_json(session_root / "filled_fields.json", warnings)
    unresolved_fields = _read_json(session_root / "unresolved_fields.json", warnings)
    approvals_required = _read_json(session_root / "approvals_required.json", warnings)
    browser_request = _read_json(session_root / "openclaw" / "browser.request.json", warnings)
    browser_result = _read_json(session_root / "openclaw" / "browser.result.json", warnings)
    report_md = _read_text(session_root / "apply_report.md", warnings)

    filled_fields_list = filled_fields if isinstance(filled_fields, list) else []
    unresolved_fields_list = unresolved_fields if isinstance(unresolved_fields, list) else []
    approvals_required_list = approvals_required if isinstance(approvals_required, list) else []

    pending_approvals = sum(1 for item in approvals_required_list if isinstance(item, dict) and item.get("status") == "pending")
    approved_approvals = sum(1 for item in approvals_required_list if isinstance(item, dict) and item.get("status") == "approved")

    company_name, role_title = _from_packet(job_id, application_state_root)
    if isinstance(summary, dict):
        company_name = summary.get("company_name") or company_name
        role_title = summary.get("role_title") or role_title

    updated_at = session.get("updated_at")
    if not isinstance(updated_at, str):
        updated_at = datetime.now(timezone.utc).isoformat()

    parsed_confidence = session.get("parse_confidence")
    parse_confidence = None
    if isinstance(parsed_confidence, (int, float)):
        parse_confidence = float(parsed_confidence)

    entry: dict[str, Any] = {
        "job_id": job_id,
        "company_name": company_name,
        "role_title": role_title,
        "mode": session.get("mode"),
        "status": session.get("status"),
        "updated_at": updated_at,
        "current_step": session.get("current_step"),
        "current_page_url": session.get("current_page_url"),
        "apply_url": session.get("apply_url"),
        "parse_confidence": parse_confidence,
        "submit_available": bool(session.get("submit_available", False)),
        "manual_submit_required": bool(session.get("manual_submit_required", True)),
        "pending_approvals": pending_approvals,
        "approved_approvals": approved_approvals,
        "approved_action_ids": session.get("approved_action_ids") or [],
        "pending_action_ids": session.get("pending_action_ids") or [],
        "unresolved_fields_count": len(unresolved_fields_list),
        "filled_fields_count": len(filled_fields_list),
        "candidate_inputs_count": len(browser_request.get("candidate_inputs") or []) if isinstance(browser_request, dict) else 0,
        "warnings": warnings,
        "artifacts": {
            "session_root": str(session_root),
            "session_path": str(session_root / "session.json"),
            "summary_path": str(session_root / "summary.json"),
            "browser_request_path": str(session_root / "openclaw" / "browser.request.json"),
            "browser_result_path": str(session_root / "openclaw" / "browser.result.json"),
            "report_path": str(session_root / "apply_report.md"),
        },
        "session": session,
        "summary": summary if isinstance(summary, dict) else {},
        "filled_fields": filled_fields_list,
        "unresolved_fields": unresolved_fields_list,
        "approvals_required": approvals_required_list,
        "browser_request": browser_request if isinstance(browser_request, dict) else {},
        "browser_result": browser_result if isinstance(browser_result, dict) else {},
        "report_markdown": report_md,
    }
    return entry, warnings


def build_payload(apply_state_root: Path, application_state_root: Path) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = []
    warnings: list[str] = []

    if apply_state_root.exists():
        for session_root in sorted(path for path in apply_state_root.iterdir() if path.is_dir()):
            entry, entry_warnings = _session_entry(session_root, application_state_root)
            warnings.extend(f"{session_root.name}:{warn}" for warn in entry_warnings)
            if entry is not None:
                sessions.append(entry)

    sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)

    totals = {
        "sessions": len(sessions),
        "awaiting_approval": sum(1 for item in sessions if item.get("status") == "awaiting_approval"),
        "ready_to_resume": sum(1 for item in sessions if item.get("status") == "ready_to_resume"),
        "awaiting_manual_submit": sum(1 for item in sessions if item.get("status") == "awaiting_manual_submit"),
        "cancelled": sum(1 for item in sessions if item.get("status") == "cancelled"),
        "with_pending_approvals": sum(1 for item in sessions if int(item.get("pending_approvals") or 0) > 0),
        "with_unresolved_fields": sum(1 for item in sessions if int(item.get("unresolved_fields_count") or 0) > 0),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "apply_state_root": str(apply_state_root),
        "application_state_root": str(application_state_root),
        "totals": totals,
        "sessions": sessions,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export apply sessions UI-safe JSON.")
    parser.add_argument("--apply-state-root", default="state/apply_sessions")
    parser.add_argument("--application-state-root", default="state/applications")
    parser.add_argument("--out", help="Output path (default: stdout)")
    args = parser.parse_args()

    payload = build_payload(Path(args.apply_state_root), Path(args.application_state_root))
    text = json.dumps(payload, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
