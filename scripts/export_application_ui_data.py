#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _count_missing_inputs(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip().startswith("- key:"))


def _job_entry(job_root: Path) -> dict:
    packet_path = job_root / "application_packet.json"
    cover_meta_path = job_root / "cover_letter.meta.json"
    answers_meta_path = job_root / "answers.meta.json"
    missing_inputs_path = job_root / "missing_inputs.yaml"
    draft_report_path = job_root / "draft_report.md"
    openclaw_dir = job_root / "openclaw"

    packet = _read_json(packet_path) or {}
    cover_meta = _read_json(cover_meta_path) or {}
    answers_meta = _read_json(answers_meta_path) or {}
    cover_letter_text = _read_text(job_root / "cover_letter.draft.md")
    answers_text = _read_text(job_root / "answers.draft.yaml")
    missing_inputs_text = _read_text(missing_inputs_path)
    draft_report_text = _read_text(draft_report_path)

    openclaw_requests = {
        "cover_letter_request": (openclaw_dir / "cover_letter.request.json").exists(),
        "answers_request": (openclaw_dir / "answers.request.json").exists(),
        "cover_letter_result": (openclaw_dir / "cover_letter.result.json").exists(),
        "answers_result": (openclaw_dir / "answers.result.json").exists(),
    }
    openclaw_status = "awaiting_results"
    if openclaw_requests["cover_letter_result"] or openclaw_requests["answers_result"]:
        openclaw_status = "results_available"
    elif openclaw_requests["cover_letter_request"] or openclaw_requests["answers_request"]:
        openclaw_status = "requests_ready"
    elif packet:
        openclaw_status = "not_requested"

    updated_candidates = [packet_path, cover_meta_path, answers_meta_path, missing_inputs_path, draft_report_path]
    updated_at = max((p.stat().st_mtime for p in updated_candidates if p.exists()), default=job_root.stat().st_mtime)
    updated_at_iso = datetime.fromtimestamp(updated_at, tz=timezone.utc).isoformat()

    return {
        "job_id": packet.get("job_id") or job_root.name,
        "company_name": packet.get("company_name"),
        "role_title": packet.get("role_title"),
        "source_name": (packet.get("source") or {}).get("source_name"),
        "prepared": packet_path.exists(),
        "questions_count": len(packet.get("application_questions") or []),
        "missing_inputs_count": _count_missing_inputs(missing_inputs_path),
        "cover_letter": {
            "ready": cover_meta_path.exists(),
            "origin": cover_meta.get("origin"),
            "created_at": cover_meta.get("created_at"),
            "text": cover_letter_text,
        },
        "answers": {
            "ready": answers_meta_path.exists(),
            "origin": answers_meta.get("origin"),
            "created_at": answers_meta.get("created_at"),
            "answer_count": answers_meta.get("answer_count"),
            "text": answers_text,
        },
        "missing_inputs_text": missing_inputs_text,
        "draft_report_text": draft_report_text,
        "packet_summary": {
            "cluster_id": packet.get("cluster_id"),
            "canonical_url": (packet.get("canonical_job") or {}).get("canonical_url"),
            "location_text": (packet.get("canonical_job") or {}).get("location_text"),
            "description_excerpt": (packet.get("canonical_job") or {}).get("description_excerpt"),
            "matched_signals": (packet.get("score") or {}).get("matched_signals") or [],
            "score_total": (packet.get("score") or {}).get("total"),
            "application_questions": packet.get("application_questions") or [],
        },
        "openclaw": {
            "status": openclaw_status,
            "requests": openclaw_requests,
        },
        "updated_at": updated_at_iso,
        "paths": {
            "job_root": str(job_root),
            "packet_path": str(packet_path) if packet_path.exists() else None,
            "draft_report_path": str(draft_report_path) if draft_report_path.exists() else None,
        },
    }


def build_payload(state_root: Path) -> dict:
    if not state_root.exists():
        apps: list[dict] = []
    else:
        apps = [_job_entry(path) for path in sorted(state_root.iterdir()) if path.is_dir()]

    totals = {
        "applications": len(apps),
        "prepared": sum(1 for app in apps if app["prepared"]),
        "cover_letters_ready": sum(1 for app in apps if app["cover_letter"]["ready"]),
        "answers_ready": sum(1 for app in apps if app["answers"]["ready"]),
        "awaiting_openclaw_results": sum(1 for app in apps if app["openclaw"]["status"] == "awaiting_results"),
    }
    return {
        "state_root": str(state_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": totals,
        "applications": apps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export UI-safe application helper summary JSON.")
    parser.add_argument("--state-root", default="state/applications")
    parser.add_argument("--out", help="Output path (default: stdout)")
    args = parser.parse_args()

    payload = build_payload(Path(args.state_root))
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
