from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from findmejobs.config.models import ProfileConfig
from findmejobs.domain.job import CanonicalJob
from findmejobs.ranking.engine import rank_job_with_feedback


@dataclass(frozen=True)
class RankingAuditResult:
    fixture_path: Path
    passed: bool
    errors: list[str]
    actual_ordered_job_ids: list[str]
    actual_scores: dict[str, float]
    actual_top_reasons: dict[str, list[str]]


def run_ranking_audit(fixture_path: Path) -> RankingAuditResult:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    profile = ProfileConfig.model_validate(payload["profile"])
    jobs_payload = payload.get("jobs") or []
    expected = payload.get("expected") or {}
    expected_order = list(expected.get("ordered_job_ids") or [])
    expected_scores = {str(key): float(value) for key, value in dict(expected.get("scores") or {}).items()}
    expected_top_reasons = {
        str(key): [str(item) for item in list(value)]
        for key, value in dict(expected.get("top_reasons") or {}).items()
    }

    scored: list[tuple[str, float, bool, dict[str, float]]] = []
    for item in jobs_payload:
        job, feedback_types = _parse_job_input(item)
        breakdown = rank_job_with_feedback(job, profile, feedback_types=feedback_types)
        scored.append((job.source_job_id, breakdown.total, not breakdown.hard_filter_reasons, breakdown.components))

    scored.sort(key=lambda row: (row[2], row[1], row[0]), reverse=True)
    actual_order = [job_id for job_id, _score, _passed, _components in scored]
    actual_scores = {job_id: round(score, 2) for job_id, score, _passed, _components in scored}
    actual_top_reasons = {job_id: _top_reasons(components) for job_id, _score, _passed, components in scored}

    errors: list[str] = []
    if expected_order and actual_order != expected_order:
        errors.append(f"order_mismatch:expected={expected_order}:actual={actual_order}")
    for job_id, expected_score in expected_scores.items():
        actual = actual_scores.get(job_id)
        if actual is None:
            errors.append(f"missing_score:{job_id}")
            continue
        if round(actual, 2) != round(expected_score, 2):
            errors.append(f"score_mismatch:{job_id}:expected={expected_score}:actual={actual}")
    for job_id, reasons in expected_top_reasons.items():
        actual = actual_top_reasons.get(job_id, [])
        if actual[: len(reasons)] != reasons:
            errors.append(f"top_reasons_mismatch:{job_id}:expected={reasons}:actual={actual}")
    return RankingAuditResult(
        fixture_path=fixture_path,
        passed=not errors,
        errors=errors,
        actual_ordered_job_ids=actual_order,
        actual_scores=actual_scores,
        actual_top_reasons=actual_top_reasons,
    )


def resolve_ranking_audit_fixture(fixture: str) -> Path:
    candidate = Path(fixture)
    if candidate.exists():
        return candidate
    project_root = Path(__file__).resolve().parents[3]
    search_paths = [
        project_root / "config" / "examples" / "ranking_audit" / f"{fixture}.json",
        project_root / "config" / "examples" / "ranking_audit" / fixture,
    ]
    for path in search_paths:
        if path.exists():
            return path
    raise FileNotFoundError(f"ranking_audit_fixture_not_found:{fixture}")


def _parse_job_input(item: dict) -> tuple[CanonicalJob, list[str]]:
    payload = dict(item)
    feedback_types = [str(value) for value in payload.pop("feedback_types", [])]
    payload.setdefault("source_id", payload.get("source_name", "fixture-source"))
    payload.setdefault("source_job_key", payload.get("source_job_id", "fixture-job"))
    now = datetime.fromisoformat("2026-03-22T00:00:00+00:00")
    payload.setdefault("first_seen_at", now.isoformat())
    payload.setdefault("last_seen_at", now.isoformat())
    payload.setdefault("company_name", "Fixture Co")
    payload.setdefault("title", "Engineer")
    return CanonicalJob.model_validate(payload), feedback_types


def _top_reasons(components: dict[str, float], *, limit: int = 3) -> list[str]:
    ranked = sorted(
        [(key, value) for key, value in components.items() if isinstance(value, (int, float)) and value > 0],
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    return [key for key, _value in ranked[:limit]]
