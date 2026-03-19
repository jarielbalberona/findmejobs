from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from findmejobs.config.loader import load_app_config, load_source_configs
from findmejobs.config.models import GreenhouseSourceConfig, RSSSourceConfig
from findmejobs.db.models import NormalizedJob, RawDocument, SourceFetchRun, SourceJob
from findmejobs.domain.source import FetchArtifact
from findmejobs.ingestion.adapters.greenhouse import GreenhouseAdapter
from findmejobs.ingestion.adapters.rss import RSSAdapter, canonical_rss_key
from findmejobs.ingestion.fetch import fetch_to_artifact
from findmejobs.ingestion.orchestrator import run_ingest
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow


def test_rss_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    body = (fixtures_dir / "rss_feed.xml").read_bytes()
    artifact = FetchArtifact(
        fetched_url="https://example.test/jobs.rss",
        final_url="https://example.test/jobs.rss",
        status_code=200,
        content_type="application/rss+xml",
        headers={},
        fetched_at=utcnow(),
        body_bytes=body,
        sha256="sha",
        storage_path="/tmp/rss.xml",
    )
    config = RSSSourceConfig(name="rss-source", kind="rss", enabled=True, feed_url="https://example.test/jobs.rss")
    records = RSSAdapter().parse(artifact, config)

    assert len(records) == 2
    assert records[0].source_job_key == "rss-job-1"
    assert records[0].company == "Example Labs"
    assert records[0].tags_raw == ["Python", "Backend"]
    assert canonical_rss_key("https://jobs.example.test/x", "Role") == canonical_rss_key("https://jobs.example.test/x", "Role")


def test_greenhouse_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        final_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "greenhouse_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/greenhouse.json",
    )
    config = GreenhouseSourceConfig(name="acme", kind="greenhouse", enabled=True, board_token="acme")
    records = GreenhouseAdapter().parse(artifact, config)

    assert len(records) == 2
    assert records[0].source_job_key == "101"
    assert records[0].location_text == "Remote, Philippines"
    assert records[0].tags_raw == ["Engineering"]


def test_fetch_retries_on_timeout_then_succeeds(monkeypatch, runtime_config_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    app_path, _, _ = runtime_config_files
    app_config = load_app_config(app_path)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=b"<rss></rss>", request=request)

    monkeypatch.setattr("tenacity.nap.sleep", lambda _: None)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    artifact = fetch_to_artifact(client, "https://example.test/jobs.rss", app_config, tmp_path, "rss-source")

    assert attempts["count"] == 3
    assert artifact.status_code == 200
    assert Path(artifact.storage_path).exists()


def test_malformed_payload_fails_visibly_without_corrupting_state(
    fixtures_dir: Path,
    migrated_runtime_config_files: tuple[Path, Path, Path],
) -> None:
    app_path, _profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    sources = [source for source in load_source_configs(sources_dir) if source.kind == "greenhouse"]
    from findmejobs.db.session import create_session_factory

    session_factory = create_session_factory(app_config.database.url)

    bad_body = (fixtures_dir / "greenhouse_bad.json").read_bytes()

    def fake_fetcher(client, url, app_config, raw_root, source_name):
        target = raw_root / source_name / "bad.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bad_body)
        return FetchArtifact(
            fetched_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            headers={},
            fetched_at=utcnow(),
            body_bytes=bad_body,
            sha256="bad-sha",
            storage_path=str(target),
        )

    with session_factory() as session:
        counts = run_ingest(session, app_config, sources, new_id, fetcher=fake_fetcher)
        assert counts["sources"] == 1
        assert counts["records"] == 0
        assert session.scalar(select(func.count()).select_from(RawDocument)) == 1
        assert session.scalar(select(func.count()).select_from(SourceJob)) == 0
        assert session.scalar(select(func.count()).select_from(NormalizedJob)) == 0
        failed_run = session.scalar(select(SourceFetchRun).where(SourceFetchRun.status == "failed"))
        assert failed_run is not None
        assert failed_run.error_code == "JSONDecodeError"
