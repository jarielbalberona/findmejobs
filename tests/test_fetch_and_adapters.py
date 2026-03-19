from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from findmejobs.config.loader import load_app_config, load_source_configs
from findmejobs.config.models import (
    AshbySourceConfig,
    BossjobPHSourceConfig,
    DirectPageSourceConfig,
    FounditPHSourceConfig,
    GreenhouseSourceConfig,
    JobStreetPHSourceConfig,
    KalibrrSourceConfig,
    LeverSourceConfig,
    RSSSourceConfig,
    SmartRecruitersSourceConfig,
)
from findmejobs.db.models import NormalizedJob, RawDocument, SourceFetchRun, SourceJob
from findmejobs.domain.source import FetchArtifact
from findmejobs.ingestion.adapters.ashby import AshbyAdapter
from findmejobs.ingestion.adapters.bossjob_ph import BossjobPHAdapter
from findmejobs.ingestion.adapters.direct_page import DirectPageAdapter
from findmejobs.ingestion.adapters.foundit_ph import FounditPHAdapter
from findmejobs.ingestion.adapters.greenhouse import GreenhouseAdapter
from findmejobs.ingestion.adapters.jobstreet_ph import JobStreetPHAdapter
from findmejobs.ingestion.adapters.kalibrr import KalibrrAdapter
from findmejobs.ingestion.adapters.lever import LeverAdapter
from findmejobs.ingestion.adapters.rss import RSSAdapter, canonical_rss_key
from findmejobs.ingestion.adapters.smartrecruiters import SmartRecruitersAdapter
from findmejobs.normalization.canonicalize import normalize_job
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
    config = GreenhouseSourceConfig(name="acme-source", kind="greenhouse", enabled=True, board_token="acme", company_name="Acme")
    records = GreenhouseAdapter().parse(artifact, config)

    assert len(records) == 2
    assert records[0].source_job_key == "101"
    assert records[0].company == "Acme"
    assert records[0].location_text == "Remote, Philippines"
    assert records[0].tags_raw == ["Engineering"]


def test_lever_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://api.lever.co/v0/postings/example?mode=json",
        final_url="https://api.lever.co/v0/postings/example?mode=json",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "lever_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/lever.json",
    )
    config = LeverSourceConfig(name="lever-source", kind="lever", enabled=True, site="example", company_name="Example Labs")
    records = LeverAdapter().parse(artifact, config)

    assert len(records) == 1
    assert records[0].source_job_key == "lever-1"
    assert records[0].company == "Example Labs"
    assert records[0].location_text == "Remote, Philippines"


def test_smartrecruiters_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://api.smartrecruiters.com/v1/companies/example/postings?limit=100",
        final_url="https://api.smartrecruiters.com/v1/companies/example/postings?limit=100",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "smartrecruiters_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/sr.json",
    )
    config = SmartRecruitersSourceConfig(
        name="smart-source",
        kind="smartrecruiters",
        enabled=True,
        company_identifier="example",
        company_name="Example Corp",
    )
    records = SmartRecruitersAdapter().parse(artifact, config)

    assert len(records) == 1
    assert records[0].source_job_key == "sr-1"
    assert records[0].company == "Example Corp"


def test_ashby_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://jobs.example.test/ashby.json",
        final_url="https://jobs.example.test/ashby.json",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "ashby_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/ashby.json",
    )
    config = AshbySourceConfig(
        name="ashby-source",
        kind="ashby",
        enabled=True,
        board_url="https://jobs.example.test/ashby.json",
        company_name="Example Infra",
    )
    records = AshbyAdapter().parse(artifact, config)

    assert len(records) == 1
    assert records[0].source_job_key == "ashby-1"
    assert records[0].company == "Example Infra"
    assert records[0].tags_raw == ["Infrastructure"]


def test_direct_page_adapter_extracts_structured_and_fallback_pages(fixtures_dir: Path) -> None:
    jsonld_artifact = FetchArtifact(
        fetched_url="https://jobs.example.test/direct/backend",
        final_url="https://jobs.example.test/direct/backend?utm_source=x",
        status_code=200,
        content_type="text/html",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "direct_job_jsonld.html").read_bytes(),
        sha256="sha",
        storage_path="/tmp/direct-jsonld.html",
    )
    fallback_artifact = FetchArtifact(
        fetched_url="https://jobs.example.test/direct/platform",
        final_url="https://jobs.example.test/direct/platform",
        status_code=200,
        content_type="text/html",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "direct_job_fallback.html").read_bytes(),
        sha256="sha2",
        storage_path="/tmp/direct-fallback.html",
    )
    config = DirectPageSourceConfig(name="direct-source", kind="direct_page", enabled=True, page_url="https://jobs.example.test/direct/backend")

    jsonld_record = DirectPageAdapter().parse(jsonld_artifact, config)[0]
    fallback_record = DirectPageAdapter().parse(fallback_artifact, config)[0]

    assert jsonld_record.apply_url == "https://jobs.example.test/direct/backend"
    assert jsonld_record.company == "Direct Co"
    assert fallback_record.title == "Platform Engineer"
    assert "Remote" in fallback_record.location_text


def test_jobstreet_ph_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://api.jobstreet.test/search",
        final_url="https://api.jobstreet.test/search",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "jobstreet_ph_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/jobstreet.json",
    )
    config = JobStreetPHSourceConfig(name="jobstreet-ph", kind="jobstreet_ph", enabled=True, board_url="https://api.jobstreet.test/search")
    records = JobStreetPHAdapter().parse(artifact, config)

    assert len(records) == 1
    assert records[0].source_job_key == "js-1001"
    assert records[0].apply_url == "https://www.jobstreet.com.ph/job/backend-engineer-1001"
    assert records[0].company == "Acme Philippines"
    assert "PHP 90,000 - 120,000 / month" == records[0].salary_raw


def test_kalibrr_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://api.kalibrr.test/jobs",
        final_url="https://api.kalibrr.test/jobs",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "kalibrr_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/kalibrr.json",
    )
    config = KalibrrSourceConfig(name="kalibrr", kind="kalibrr", enabled=True, board_url="https://api.kalibrr.test/jobs")
    records = KalibrrAdapter().parse(artifact, config)

    assert len(records) == 1
    assert records[0].source_job_key == "kal-2001"
    assert records[0].company == "Kalibrr Example Co"
    assert records[0].location_text == "Makati City"
    assert records[0].salary_raw == "PHP 110,000 - 150,000 / month"


def test_bossjob_ph_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://api.bossjob.test/jobs",
        final_url="https://api.bossjob.test/jobs",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "bossjob_ph_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/bossjob.json",
    )
    config = BossjobPHSourceConfig(name="bossjob", kind="bossjob_ph", enabled=True, board_url="https://api.bossjob.test/jobs")
    records = BossjobPHAdapter().parse(artifact, config)

    assert len(records) == 1
    assert records[0].source_job_key == "boss-3001"
    assert records[0].company == "Bossjob Data Corp"
    assert records[0].location_text == "Pasig, Metro Manila, Philippines"
    assert records[0].salary_raw == "PHP 100,000 - 140,000 / month"


def test_foundit_ph_adapter_parses_realistic_fixture(fixtures_dir: Path) -> None:
    artifact = FetchArtifact(
        fetched_url="https://api.foundit.test/jobs",
        final_url="https://api.foundit.test/jobs",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=(fixtures_dir / "foundit_ph_jobs.json").read_bytes(),
        sha256="sha",
        storage_path="/tmp/foundit.json",
    )
    config = FounditPHSourceConfig(name="foundit", kind="foundit_ph", enabled=True, board_url="https://api.foundit.test/jobs")
    records = FounditPHAdapter().parse(artifact, config)

    assert len(records) == 1
    assert records[0].source_job_key == "foundit-4001"
    assert records[0].company == "Foundit Reliability Inc"
    assert records[0].location_text == "Remote, Philippines"
    assert records[0].salary_raw == "PHP 130,000 - 170,000 / month"


@pytest.mark.parametrize(
    ("config", "body", "expected_error"),
    [
        (JobStreetPHSourceConfig(name="jobstreet", kind="jobstreet_ph", enabled=True, board_url="https://example.test/jobstreet"), b"{}", "invalid_jobstreet_ph_payload"),
        (KalibrrSourceConfig(name="kalibrr", kind="kalibrr", enabled=True, board_url="https://example.test/kalibrr"), b"{}", "invalid_kalibrr_payload"),
        (BossjobPHSourceConfig(name="bossjob", kind="bossjob_ph", enabled=True, board_url="https://example.test/bossjob"), b"{}", "invalid_bossjob_ph_payload"),
        (FounditPHSourceConfig(name="foundit", kind="foundit_ph", enabled=True, board_url="https://example.test/foundit"), b"{}", "invalid_foundit_ph_payload"),
    ],
)
def test_ph_board_adapters_fail_visibly_on_malformed_payload(config, body: bytes, expected_error: str) -> None:
    artifact = FetchArtifact(
        fetched_url="https://example.test/jobs",
        final_url="https://example.test/jobs",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=body,
        sha256="sha",
        storage_path="/tmp/board.json",
    )
    adapter = {
        "jobstreet_ph": JobStreetPHAdapter(),
        "kalibrr": KalibrrAdapter(),
        "bossjob_ph": BossjobPHAdapter(),
        "foundit_ph": FounditPHAdapter(),
    }[config.kind]

    with pytest.raises(ValueError, match=expected_error):
        adapter.parse(artifact, config)


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


def test_ph_board_records_normalize_into_canonical_jobs(fixtures_dir: Path) -> None:
    now = utcnow()
    configs_and_bodies = [
        (JobStreetPHAdapter(), JobStreetPHSourceConfig(name="jobstreet", kind="jobstreet_ph", enabled=True, board_url="https://example.test/jobstreet"), fixtures_dir / "jobstreet_ph_jobs.json"),
        (KalibrrAdapter(), KalibrrSourceConfig(name="kalibrr", kind="kalibrr", enabled=True, board_url="https://example.test/kalibrr"), fixtures_dir / "kalibrr_jobs.json"),
        (BossjobPHAdapter(), BossjobPHSourceConfig(name="bossjob", kind="bossjob_ph", enabled=True, board_url="https://example.test/bossjob"), fixtures_dir / "bossjob_ph_jobs.json"),
        (FounditPHAdapter(), FounditPHSourceConfig(name="foundit", kind="foundit_ph", enabled=True, board_url="https://example.test/foundit"), fixtures_dir / "foundit_ph_jobs.json"),
    ]

    for idx, (adapter, config, fixture_path) in enumerate(configs_and_bodies, start=1):
        artifact = FetchArtifact(
            fetched_url=str(config.board_url),
            final_url=str(config.board_url),
            status_code=200,
            content_type="application/json",
            headers={},
            fetched_at=now,
            body_bytes=fixture_path.read_bytes(),
            sha256=f"sha-{idx}",
            storage_path=f"/tmp/board-{idx}.json",
        )
        record = adapter.parse(artifact, config)[0]
        job = normalize_job(f"source-job-{idx}", f"source-{idx}", now, record)

        assert job.company_name
        assert job.title
        assert job.canonical_url is not None
        assert job.description_text
        if record.salary_raw:
            assert job.salary_currency == "PHP"
