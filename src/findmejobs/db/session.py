from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker


def create_engine_with_sqlite_pragmas(database_url: str) -> Engine:
    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()

    return engine


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine_with_sqlite_pragmas(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def database_file_from_url(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        return Path(database_url.removeprefix(prefix))
    return None


def fetch_pragma(session: Session, name: str) -> str | None:
    result = session.execute(text(f"PRAGMA {name};"))
    row = result.first()
    if not row:
        return None
    return str(row[0])
