from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from findmejobs.db.models import Source, SourceFetchRun
from findmejobs.db.session import database_file_from_url, fetch_pragma


def run_doctor(session: Session, database_url: str, required_paths: list[Path]) -> list[str]:
    errors: list[str] = []
    db_path = database_file_from_url(database_url)
    if db_path is not None and not db_path.exists():
        errors.append("database_missing")
    if fetch_pragma(session, "journal_mode") != "wal":
        errors.append("sqlite_wal_disabled")
    if fetch_pragma(session, "foreign_keys") != "1":
        errors.append("sqlite_foreign_keys_disabled")
    for path in required_paths:
        if not path.exists():
            errors.append(f"missing_path:{path}")
    enabled_sources = session.scalar(select(func.count()).select_from(Source).where(Source.enabled.is_(True)))
    if enabled_sources in (None, 0):
        errors.append("no_enabled_sources")
    return errors
