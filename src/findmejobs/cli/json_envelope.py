"""Stable JSON envelope for agent-facing CLI `--json` output."""

from __future__ import annotations

import json
from typing import Any

import typer

from findmejobs import __version__ as FINDMEJOBS_VERSION
from findmejobs.utils.time import utcnow


def cli_envelope(
    command: str,
    ok: bool,
    *,
    summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    artifacts: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "summary": summary or {},
        "warnings": warnings or [],
        "errors": errors or [],
        "artifacts": artifacts or {},
        "meta": meta or {},
    }


def emit_envelope(json_out: bool, envelope: dict[str, Any], *, text: str | None = None) -> None:
    if json_out:
        typer.echo(json.dumps(envelope, indent=2, default=str))
        return
    if text is not None:
        typer.echo(text)


def legacy_command_name(payload: dict[str, Any]) -> str | None:
    raw = payload.get("command")
    return str(raw) if raw is not None else None


def envelope_from_legacy_payload(payload: dict[str, Any], *, command: str | None = None) -> dict[str, Any]:
    """Map older ad-hoc CLI payloads into the standard envelope (lossless: full payload in summary.legacy)."""
    cmd = command or legacy_command_name(payload) or "unknown"
    status = str(payload.get("status", "")).casefold()
    ok = status in {"ok", "success", ""} and "failed" not in status
    if "error" in payload or payload.get("status") == "failed":
        ok = False
    errors: list[str] = []
    if isinstance(payload.get("errors"), list):
        errors = [str(e) for e in payload["errors"]]
    elif payload.get("error"):
        errors = [str(payload["error"])]
    elif payload.get("error_message"):
        errors = [str(payload["error_message"])]
    warnings: list[str] = []
    if isinstance(payload.get("hints"), dict):
        warnings = [f"{k}: {v}" for k, v in payload["hints"].items()]
    summary = {k: v for k, v in payload.items() if k not in {"hints"}}
    summary["legacy"] = payload
    artifacts: dict[str, Any] = {}
    if "ui_export" in payload:
        artifacts["ui_export"] = payload["ui_export"]
    return cli_envelope(cmd, ok, summary=summary, warnings=warnings, errors=errors, artifacts=artifacts)


def meta_standard(**extra: Any) -> dict[str, Any]:
    m: dict[str, Any] = {"cli_version": FINDMEJOBS_VERSION, "generated_at": utcnow().isoformat()}
    m.update(extra)
    return m


def merge_ui_export_artifact(envelope: dict[str, Any], ui_export: dict[str, Any] | None) -> dict[str, Any]:
    if not ui_export:
        return envelope
    arts = dict(envelope.get("artifacts") or {})
    arts["ui_export"] = ui_export
    envelope["artifacts"] = arts
    if ui_export.get("status") not in (None, "ok", "skipped") and ui_export.get("status") != "ok":
        warns = list(envelope.get("warnings") or [])
        warns.append(f"ui_export:{ui_export.get('status')}:{ui_export.get('message', '')}")
        envelope["warnings"] = warns
    return envelope
