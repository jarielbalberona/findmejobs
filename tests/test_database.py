from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from findmejobs.db.models import Source
from findmejobs.db.session import fetch_pragma
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow


def test_sqlite_db_initializes_with_wal(session_factory) -> None:
    with session_factory() as session:
        assert fetch_pragma(session, "journal_mode") == "wal"
        assert fetch_pragma(session, "foreign_keys") == "1"


def test_migrations_apply_cleanly_and_hot_indexes_exist(migrated_db_url: str) -> None:
    from findmejobs.db.session import create_engine_with_sqlite_pragmas

    engine = create_engine_with_sqlite_pragmas(migrated_db_url)
    inspector = inspect(engine)
    assert "sources" in inspector.get_table_names()
    assert "job_feedback" in inspector.get_table_names()
    assert "digests" in inspector.get_table_names()
    assert "delivery_events" in inspector.get_table_names()
    normalized_indexes = {index["name"] for index in inspector.get_indexes("normalized_jobs")}
    review_indexes = {index["name"] for index in inspector.get_indexes("review_packets")}
    feedback_indexes = {index["name"] for index in inspector.get_indexes("job_feedback")}
    assert "ix_normalized_jobs_canonical_url" in normalized_indexes
    assert "ix_normalized_jobs_company_name" in normalized_indexes
    assert "ix_review_packets_status" in review_indexes
    assert "ix_job_feedback_feedback_type" in feedback_indexes


def test_uniqueness_constraints_behave_correctly(session_factory) -> None:
    with session_factory() as session:
        session.add(
            Source(
                id=new_id(),
                name="unique-source",
                kind="rss",
                enabled=True,
                config_json={},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.commit()
        session.add(
            Source(
                id=new_id(),
                name="unique-source",
                kind="rss",
                enabled=True,
                config_json={},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
