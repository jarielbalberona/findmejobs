from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from findmejobs.config.loader import load_app_config, load_source_configs
from findmejobs.db.models import JobCluster, NormalizedJob, RawDocument, SourceFetchRun, SourceJob
from findmejobs.db.session import create_session_factory
from findmejobs.domain.source import FetchArtifact
from findmejobs.ingestion.orchestrator import run_ingest
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow


def test_fetched_payload_is_stored_before_normalization_and_metadata_is_preserved(
    fixtures_dir: Path,
    migrated_runtime_config_files: tuple[Path, Path, Path],
) -> None:
    app_path, _profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    sources = [source for source in load_source_configs(sources_dir) if source.kind == "rss"]
    session_factory = create_session_factory(app_config.database.url)
    body = (fixtures_dir / "rss_feed.xml").read_bytes()

    def fake_fetcher(client, url, app_config, raw_root, source_name):
        target = raw_root / source_name / "feed.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return FetchArtifact(
            fetched_url=url,
            final_url=url,
            status_code=200,
            content_type="application/rss+xml",
            headers={"etag": "W/test"},
            fetched_at=utcnow(),
            body_bytes=body,
            sha256="rss-sha",
            storage_path=str(target),
        )

    with session_factory() as session:
        counts = run_ingest(session, app_config, sources, new_id, fetcher=fake_fetcher)
        raw_document = session.scalar(select(RawDocument))
        assert counts["records"] == 2
        assert raw_document is not None
        assert raw_document.content_type == "application/rss+xml"
        assert Path(raw_document.storage_path).exists()
        assert session.scalar(select(func.count()).select_from(SourceJob)) == 2
        assert session.scalar(select(func.count()).select_from(NormalizedJob)) == 2


def test_rerunning_same_source_is_idempotent_for_core_rows(
    fixtures_dir: Path,
    migrated_runtime_config_files: tuple[Path, Path, Path],
) -> None:
    app_path, _profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    sources = [source for source in load_source_configs(sources_dir) if source.kind == "rss"]
    session_factory = create_session_factory(app_config.database.url)
    body = (fixtures_dir / "rss_feed.xml").read_bytes()

    def fake_fetcher(client, url, app_config, raw_root, source_name):
        target = raw_root / source_name / "feed.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return FetchArtifact(
            fetched_url=url,
            final_url=url,
            status_code=200,
            content_type="application/rss+xml",
            headers={},
            fetched_at=utcnow(),
            body_bytes=body,
            sha256="rss-sha",
            storage_path=str(target),
        )

    with session_factory() as session:
        run_ingest(session, app_config, sources, new_id, fetcher=fake_fetcher)
        run_ingest(session, app_config, sources, new_id, fetcher=fake_fetcher)
        assert session.scalar(select(func.count()).select_from(RawDocument)) == 1
        assert session.scalar(select(func.count()).select_from(SourceJob)) == 2
        assert session.scalar(select(func.count()).select_from(NormalizedJob)) == 2
        assert session.scalar(select(func.count()).select_from(JobCluster)) == 2
        assert session.scalar(select(func.count()).select_from(SourceFetchRun)) == 2
