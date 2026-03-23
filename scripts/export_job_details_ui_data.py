#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
import tomllib

REQUIRED_TABLES = ("normalized_jobs", "source_jobs", "sources")


def _load_database_url(app_config_path: Path) -> str:
    payload = tomllib.loads(app_config_path.read_text(encoding="utf-8"))
    database = payload.get("database") or {}
    url = database.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError(f"missing database.url in {app_config_path}")
    return url


def _sqlite_path_from_url(database_url: str, app_config_path: Path) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError(f"only sqlite URLs are supported, got: {database_url}")
    raw = database_url.removeprefix(prefix)
    path = Path(raw)
    if path.is_absolute():
        return path
    return (app_config_path.parent.parent / path).resolve()


def _missing_required_tables(conn: sqlite3.Connection) -> list[str]:
    table_names = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return [name for name in REQUIRED_TABLES if name not in table_names]


def _query_job_details(db_path: Path, *, description_max_chars: int | None) -> tuple[dict[str, dict], list[str]]:
    sql = """
    SELECT
      nj.id AS job_id,
      nj.title,
      nj.company_name,
      nj.location_text,
      nj.location_type,
      nj.country_code,
      nj.city,
      nj.region,
      nj.seniority,
      nj.employment_type,
      nj.salary_min,
      nj.salary_max,
      nj.salary_currency,
      nj.salary_period,
      nj.posted_at,
      nj.first_seen_at,
      nj.last_seen_at,
      nj.canonical_url,
      nj.description_text,
      nj.tags_json,
      s.name AS source_name,
      s.kind AS source_kind
    FROM normalized_jobs nj
    JOIN source_jobs sj ON sj.id = nj.source_job_id
    JOIN sources s ON s.id = sj.source_id
    WHERE nj.normalization_status = 'valid'
    """
    out: dict[str, dict] = {}
    warnings: list[str] = []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        missing_tables = _missing_required_tables(conn)
        if missing_tables:
            warnings.append(f"missing_required_tables:{','.join(missing_tables)}")
            return out, warnings
        try:
            rows = conn.execute(sql)
        except sqlite3.OperationalError as exc:
            warnings.append(f"query_failed:{exc}")
            return out, warnings
        for row in rows:
            description = row["description_text"] or ""
            if description_max_chars is not None and description_max_chars > 0 and len(description) > description_max_chars:
                description = description[:description_max_chars].rstrip() + "\n... [truncated]"
            try:
                tags = json.loads(row["tags_json"] or "[]")
            except json.JSONDecodeError:
                tags = []
            out[row["job_id"]] = {
                "job_id": row["job_id"],
                "title": row["title"],
                "company_name": row["company_name"],
                "location_text": row["location_text"],
                "location_type": row["location_type"],
                "country_code": row["country_code"],
                "city": row["city"],
                "region": row["region"],
                "seniority": row["seniority"],
                "employment_type": row["employment_type"],
                "salary_min": row["salary_min"],
                "salary_max": row["salary_max"],
                "salary_currency": row["salary_currency"],
                "salary_period": row["salary_period"],
                "posted_at": row["posted_at"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "canonical_url": row["canonical_url"],
                "source_name": row["source_name"],
                "source_kind": row["source_kind"],
                "tags": tags if isinstance(tags, list) else [],
                "description_text": description,
            }
    return out, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Export job detail JSON for UI.")
    parser.add_argument("--app-config-path", default="config/app.toml")
    parser.add_argument("--out", required=True)
    parser.add_argument("--description-max-chars", type=int, default=0, help="0 means no truncation")
    args = parser.parse_args()

    app_config_path = Path(args.app_config_path)
    database_url = _load_database_url(app_config_path)
    db_path = _sqlite_path_from_url(database_url, app_config_path)
    max_chars = None if args.description_max_chars <= 0 else args.description_max_chars
    jobs, warnings = _query_job_details(db_path, description_max_chars=max_chars)

    payload = {"database_url": database_url, "db_path": str(db_path), "jobs": jobs, "warnings": warnings}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if warnings:
        print("; ".join(warnings), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
