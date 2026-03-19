from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from findmejobs.config.loader import load_app_config, load_profile_config
from findmejobs.config.models import AshbySourceConfig, DirectPageSourceConfig, LeverSourceConfig, RSSSourceConfig, SmartRecruitersSourceConfig
from findmejobs.db.models import (
    DeliveryEvent,
    Digest,
    DigestItem,
    JobCluster,
    JobFeedback,
    JobScore,
    NormalizedJob,
    OpenClawReview,
    PipelineRun,
    Profile,
    RankModel,
    RawDocument,
    ReviewPacket,
    Source,
    SourceFetchRun,
    SourceJob,
)
from findmejobs.db.repositories import upsert_job_score, upsert_profile, upsert_rank_model
from findmejobs.db.session import create_session_factory
from findmejobs.delivery.digest import build_digest_candidates, send_digest
from findmejobs.delivery.email import EmailDeliveryError, SMTPEmailSender
from findmejobs.domain.job import CanonicalJob
from findmejobs.feedback import feedback_types_for_job, record_feedback
from findmejobs.ingestion.adapters.ashby import AshbyAdapter
from findmejobs.ingestion.adapters.direct_page import DirectPageAdapter
from findmejobs.ingestion.adapters.lever import LeverAdapter
from findmejobs.ingestion.adapters.smartrecruiters import SmartRecruitersAdapter
from findmejobs.ingestion.orchestrator import run_ingest
from findmejobs.observability.doctor import run_doctor
from findmejobs.ranking.engine import rank_job_with_feedback
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow


def _job(**overrides) -> CanonicalJob:
    now = utcnow()
    payload = {
        "source_job_id": "source-job-id",
        "source_id": "source-id",
        "source_job_key": "job-1",
        "canonical_url": "https://example.test/jobs/1",
        "company_name": "Example",
        "title": "Backend Engineer",
        "location_text": "Remote, Philippines",
        "location_type": "remote",
        "country_code": "PH",
        "description_text": "Python SQL AWS Asia/Manila",
        "tags": ["python", "sql", "aws"],
        "first_seen_at": now,
        "last_seen_at": now,
        "posted_at": now - timedelta(days=2),
        "source_name": "lever-source",
        "source_trust_weight": 1.4,
        "source_priority": 10,
    }
    payload.update(overrides)
    return CanonicalJob(**payload)


def _seed_reviewable_cluster(
    session,
    profile,
    *,
    cluster_id: str,
    source_id: str,
    source_name: str,
    source_kind: str,
    normalized_id: str,
    source_job_id: str,
    source_job_key: str,
    title: str,
    company: str,
    location_text: str,
    location_type: str = "remote",
    country_code: str | None = "PH",
    description_text: str = "Python SQL AWS Asia/Manila",
    score_total: float = 90.0,
    components: dict[str, float] | None = None,
    decision: str = "keep",
    canonical_url: str | None = None,
):
    now = utcnow()
    source = Source(
        id=source_id,
        name=source_name,
        kind=source_kind,
        enabled=True,
        priority=10,
        trust_weight=1.3,
        fetch_cap=50,
        config_json={},
        created_at=now,
        updated_at=now,
        last_successful_run_at=now,
    )
    fetch_run = SourceFetchRun(
        id=f"fetch-{source_id}",
        source_id=source.id,
        started_at=now,
        finished_at=now,
        status="success",
        attempt_count=1,
        item_count=1,
        seen_count=1,
        inserted_count=1,
        updated_count=0,
        failed_count=0,
        parse_error_count=0,
        dedupe_merge_count=0,
        normalized_valid_count=1,
    )
    raw = RawDocument(
        id=f"raw-{source_id}",
        source_id=source.id,
        fetch_run_id=fetch_run.id,
        url=canonical_url or f"https://jobs.example.test/{source_job_key}",
        canonical_url=canonical_url or f"https://jobs.example.test/{source_job_key}",
        content_type="application/json",
        http_status=200,
        sha256=f"sha-{source_id}",
        storage_path=f"/tmp/{source_id}.json",
        fetched_at=now,
    )
    source_job = SourceJob(
        id=source_job_id,
        source_id=source.id,
        raw_document_id=raw.id,
        fetch_run_id=fetch_run.id,
        source_job_key=source_job_key,
        source_url=canonical_url or f"https://jobs.example.test/{source_job_key}",
        apply_url=canonical_url or f"https://jobs.example.test/{source_job_key}",
        payload_json={
            "title": title,
            "company": company,
            "location_text": location_text,
            "description": description_text,
            "posted_at": now.isoformat(),
        },
        seen_at=now,
    )
    normalized = NormalizedJob(
        id=normalized_id,
        source_job_id=source_job.id,
        canonical_url=canonical_url or f"https://jobs.example.test/{source_job_key}",
        company_name=company,
        title=title,
        location_text=location_text,
        location_type=location_type,
        country_code=country_code,
        description_text=description_text,
        description_sha256=f"desc-{normalized_id}",
        tags_json=["python", "sql", "aws"],
        posted_at=now,
        first_seen_at=now,
        last_seen_at=now,
        normalization_status="valid",
        normalization_errors_json=[],
    )
    cluster = JobCluster(
        id=cluster_id,
        cluster_key=cluster_id,
        representative_job_id=normalized.id,
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    session.add(fetch_run)
    session.flush()
    session.add(raw)
    session.flush()
    session.add(source_job)
    session.flush()
    session.add(normalized)
    session.flush()
    session.add(cluster)
    session.flush()

    profile_row = upsert_profile(session, profile, new_id)
    rank_model = upsert_rank_model(session, profile, new_id)
    score = JobScore(
        id=new_id(),
        cluster_id=cluster_id,
        profile_id=profile_row.id,
        rank_model_id=rank_model.id,
        passed_hard_filters=True,
        hard_filter_reasons_json=[],
        score_total=score_total,
        score_breakdown_json=components or {"title_alignment": 30.0, "must_have_skills": 20.0},
        scored_at=now,
    )
    session.add(score)
    session.flush()
    packet = ReviewPacket(
        id=f"packet-{cluster_id}",
        cluster_id=cluster_id,
        job_score_id=score.id,
        packet_version="v1",
        packet_json={"packet_id": f"packet-{cluster_id}"},
        packet_sha256=f"sha-{cluster_id}",
        status="exported",
        built_at=now,
        exported_at=now,
    )
    session.add(packet)
    session.flush()
    review = OpenClawReview(
        id=f"review-{cluster_id}",
        review_packet_id=packet.id,
        provider_review_id=f"provider-{cluster_id}",
        decision=decision,
        confidence_label="high",
        reasons_json=["good fit"],
        draft_summary="good fit",
        draft_actions_json=["apply"],
        raw_response_json={},
        reviewed_at=now,
        imported_at=now,
    )
    session.add(review)
    session.flush()
    return {"cluster_id": cluster_id, "score_id": score.id, "review_id": review.id}


@pytest.mark.parametrize(
    ("adapter", "config", "body", "expected_error"),
    [
        (LeverAdapter(), LeverSourceConfig(name="lever", kind="lever", enabled=True, site="example"), b"{}", "invalid_lever_payload"),
        (
            SmartRecruitersAdapter(),
            SmartRecruitersSourceConfig(name="sr", kind="smartrecruiters", enabled=True, company_identifier="example"),
            b"{}",
            "invalid_smartrecruiters_payload",
        ),
        (
            AshbyAdapter(),
            AshbySourceConfig(name="ashby", kind="ashby", enabled=True, board_url="https://jobs.example.test/ashby"),
            b"{}",
            "invalid_ashby_payload",
        ),
    ],
)
def test_malformed_adapter_payloads_fail_clearly(adapter, config, body: bytes, expected_error: str) -> None:
    artifact = httpx.Response(200, content=body, headers={"content-type": "application/json"}, request=httpx.Request("GET", "https://example.test")).read()
    from findmejobs.domain.source import FetchArtifact

    fetch_artifact = FetchArtifact(
        fetched_url="https://example.test",
        final_url="https://example.test",
        status_code=200,
        content_type="application/json",
        headers={},
        fetched_at=utcnow(),
        body_bytes=body,
        sha256="sha",
        storage_path="/tmp/test.json",
    )
    with pytest.raises(ValueError, match=expected_error):
        adapter.parse(fetch_artifact, config)


def test_direct_page_invalid_page_fails_visibly_without_normalized_rows(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, _profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    source = DirectPageSourceConfig(name="direct", kind="direct_page", enabled=True, page_url="https://example.test/jobs/nope")

    def fake_fetcher(client, url, app_config, raw_root, source_name):
        target = raw_root / source_name / "page.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<html><body><p>hello world</p></body></html>", encoding="utf-8")
        from findmejobs.domain.source import FetchArtifact

        return FetchArtifact(
            fetched_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            headers={},
            fetched_at=utcnow(),
            body_bytes=target.read_bytes(),
            sha256="html-sha",
            storage_path=str(target),
        )

    with session_factory() as session:
        counts = run_ingest(session, app_config, [source], new_id, fetcher=fake_fetcher)
        assert counts["failed_sources"] == 1
        assert session.scalar(select(func.count()).select_from(RawDocument)) == 1
        assert session.scalar(select(func.count()).select_from(SourceJob)) == 0
        assert session.scalar(select(func.count()).select_from(NormalizedJob)) == 0
        failed = session.scalar(select(SourceFetchRun).where(SourceFetchRun.status == "failed"))
        assert failed is not None
        assert failed.error_message == "direct_page_no_job_data"


def test_run_ingest_respects_disabled_sources_and_fetch_cap(fixtures_dir: Path, migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, _profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    enabled = LeverSourceConfig(name="lever-source", kind="lever", enabled=True, site="example", fetch_cap=1)
    disabled = RSSSourceConfig(name="rss-disabled", kind="rss", enabled=False, feed_url="https://example.test/jobs.rss")

    def fake_fetcher(client, url, app_config, raw_root, source_name):
        target = raw_root / source_name / "jobs.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((fixtures_dir / "lever_jobs.json").read_bytes())
        from findmejobs.domain.source import FetchArtifact

        return FetchArtifact(
            fetched_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            headers={},
            fetched_at=utcnow(),
            body_bytes=target.read_bytes(),
            sha256=f"{source_name}-sha",
            storage_path=str(target),
        )

    with session_factory() as session:
        counts = run_ingest(session, app_config, [enabled, disabled], new_id, fetcher=fake_fetcher)
        assert counts["sources"] == 1
        assert counts["records"] == 1
        assert session.scalar(select(func.count()).select_from(SourceFetchRun)) == 1


def test_digest_candidate_selection_is_deterministic_and_suppresses_recent_or_feedback(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        first = _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-a",
            source_id="source-a",
            source_name="lever-a",
            source_kind="lever",
            normalized_id="normalized-a",
            source_job_id="source-job-a",
            source_job_key="job-a",
            title="Backend Engineer",
            company="Acme",
            location_text="Remote, Philippines",
            score_total=92.0,
            components={"must_have_skills": 35.0, "title_alignment": 30.0, "recency": 4.0},
        )
        second = _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-b",
            source_id="source-b",
            source_name="ashby-b",
            source_kind="ashby",
            normalized_id="normalized-b",
            source_job_id="source-job-b",
            source_job_key="job-b",
            title="Platform Engineer",
            company="Bravo",
            location_text="Remote, Philippines",
            score_total=89.0,
            components={"title_alignment": 28.0, "source_trust": 4.0},
        )
        _suppressed = _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-c",
            source_id="source-c",
            source_name="sr-c",
            source_kind="smartrecruiters",
            normalized_id="normalized-c",
            source_job_id="source-job-c",
            source_job_key="job-c",
            title="Staff Engineer",
            company="Charlie",
            location_text="Remote, Philippines",
            score_total=95.0,
        )
        session.add(JobFeedback(id=new_id(), cluster_id="cluster-c", feedback_type="ignore", created_at=utcnow()))
        existing_digest = Digest(
            id="digest-old",
            channel=app_config.delivery.channel,
            digest_date="2026-03-18",
            window_start=utcnow() - timedelta(days=1),
            window_end=utcnow(),
            status="sent",
            subject="old",
            body_text="old",
            sent_at=utcnow(),
        )
        session.add(existing_digest)
        session.flush()
        session.add(
            DigestItem(
                id="item-old",
                digest_id=existing_digest.id,
                cluster_id=second["cluster_id"],
                review_id=second["review_id"],
                job_score_id=second["score_id"],
                position=1,
                item_json={},
                score_at_send=89.0,
            )
        )
        session.commit()

        first_candidates = build_digest_candidates(session, profile, limit=10)
        second_candidates = build_digest_candidates(session, profile, limit=10)

    assert [item.cluster_id for item in first_candidates] == ["cluster-a"]
    assert [item.cluster_id for item in second_candidates] == ["cluster-a"]
    assert first_candidates[0].why == "must have skills, title alignment"


def test_send_digest_is_idempotent_and_resend_creates_new_digest(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-1",
            source_id="source-1",
            source_name="lever-source",
            source_kind="lever",
            normalized_id="normalized-1",
            source_job_id="source-job-1",
            source_job_key="job-1",
            title="Backend Engineer",
            company="Example",
            location_text="Remote, Philippines",
        )
        session.commit()

        class FakeSender:
            def send(self, *, subject: str, body_text: str) -> str:
                return "provider-1"

        first = send_digest(session, app_config, profile, id_factory=new_id, sender=FakeSender(), digest_date="2026-03-19")
        session.commit()
        second = send_digest(session, app_config, profile, id_factory=new_id, sender=FakeSender(), digest_date="2026-03-19")
        session.commit()
        resent = send_digest(
            session,
            app_config,
            profile,
            id_factory=new_id,
            sender=FakeSender(),
            digest_date="2026-03-19",
            resend_of_digest_id=first.id,
        )
        session.commit()

        assert first.id == second.id
        assert resent.id != first.id
        assert resent.resend_of_digest_id == first.id
        assert session.scalar(select(func.count()).select_from(DeliveryEvent)) == 2


def test_send_digest_failure_records_failed_event(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-1",
            source_id="source-1",
            source_name="lever-source",
            source_kind="lever",
            normalized_id="normalized-1",
            source_job_id="source-job-1",
            source_job_key="job-1",
            title="Backend Engineer",
            company="Example",
            location_text="Remote, Philippines",
        )
        session.commit()

        class FailingSender:
            def __init__(self) -> None:
                self.last_attempt_count = 3

            def send(self, *, subject: str, body_text: str) -> str:
                raise RuntimeError("smtp_down")

        with pytest.raises(RuntimeError, match="smtp_down"):
            send_digest(session, app_config, profile, id_factory=new_id, sender=FailingSender(), digest_date="2026-03-19")
        session.commit()
        event = session.scalar(select(DeliveryEvent).order_by(DeliveryEvent.created_at.desc()))
        digest = session.scalar(select(Digest).order_by(Digest.digest_date.desc()))
        assert event is not None
        assert event.status == "failed"
        assert "smtp_down" in (event.error_message or "")
        assert digest is not None
        assert digest.status == "failed"
        assert event.attempt == 3


def test_cluster_representative_prefers_higher_trust_and_priority(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        from findmejobs.dedupe.clustering import assign_job_cluster

        source_low = Source(
            id="source-low",
            name="low",
            kind="rss",
            enabled=True,
            priority=1,
            trust_weight=0.5,
            config_json={},
            created_at=now,
            updated_at=now,
        )
        source_high = Source(
            id="source-high",
            name="high",
            kind="lever",
            enabled=True,
            priority=20,
            trust_weight=1.8,
            config_json={},
            created_at=now,
            updated_at=now,
        )
        session.add_all([source_low, source_high])
        session.flush()
        for source_id in ("source-low", "source-high"):
            session.add(
                SourceFetchRun(
                    id=f"fetch-{source_id}",
                    source_id=source_id,
                    started_at=now,
                    status="success",
                    attempt_count=1,
                    item_count=1,
                )
            )
        session.flush()
        low_raw = RawDocument(
            id="raw-low",
            source_id="source-low",
            fetch_run_id="fetch-source-low",
            url="https://jobs.example.test/shared",
            canonical_url="https://jobs.example.test/shared",
            content_type="application/json",
            http_status=200,
            sha256="sha-low",
            storage_path="/tmp/raw-low.json",
            fetched_at=now,
        )
        high_raw = RawDocument(
            id="raw-high",
            source_id="source-high",
            fetch_run_id="fetch-source-high",
            url="https://jobs.example.test/shared",
            canonical_url="https://jobs.example.test/shared",
            content_type="application/json",
            http_status=200,
            sha256="sha-high",
            storage_path="/tmp/raw-high.json",
            fetched_at=now,
        )
        session.add_all([low_raw, high_raw])
        session.flush()
        low_source_job = SourceJob(
            id="source-job-low",
            source_id="source-low",
            raw_document_id=low_raw.id,
            fetch_run_id="fetch-source-low",
            source_job_key="shared",
            source_url="https://jobs.example.test/shared",
            apply_url="https://jobs.example.test/shared",
            payload_json={},
            seen_at=now,
        )
        high_source_job = SourceJob(
            id="source-job-high",
            source_id="source-high",
            raw_document_id=high_raw.id,
            fetch_run_id="fetch-source-high",
            source_job_key="shared-other",
            source_url="https://jobs.example.test/shared",
            apply_url="https://jobs.example.test/shared",
            payload_json={},
            seen_at=now,
        )
        session.add_all([low_source_job, high_source_job])
        session.flush()
        low_job = NormalizedJob(
            id="normalized-low",
            source_job_id=low_source_job.id,
            canonical_url="https://jobs.example.test/shared",
            company_name="Example",
            title="Backend Engineer",
            location_text="Remote, Philippines",
            location_type="remote",
            country_code="PH",
            description_text="Short",
            description_sha256="desc-low",
            tags_json=["python"],
            posted_at=now,
            first_seen_at=now,
            last_seen_at=now,
            normalization_status="valid",
            normalization_errors_json=[],
        )
        high_job = NormalizedJob(
            id="normalized-high",
            source_job_id=high_source_job.id,
            canonical_url="https://jobs.example.test/shared",
            company_name="Example",
            title="Backend Engineer",
            location_text="Remote, Philippines",
            location_type="remote",
            country_code="PH",
            description_text="Tiny",
            description_sha256="desc-high",
            tags_json=["python"],
            posted_at=now,
            first_seen_at=now,
            last_seen_at=now,
            normalization_status="valid",
            normalization_errors_json=[],
        )
        session.add_all([low_job, high_job])
        session.flush()

        cluster, merged_first = assign_job_cluster(session, low_job, new_id)
        cluster, merged_second = assign_job_cluster(session, high_job, new_id)
        session.commit()

        assert merged_first is False
        assert merged_second is True
        assert cluster.representative_job_id == "normalized-high"


def test_smtp_sender_tracks_actual_attempt_count(monkeypatch) -> None:
    class FakeSMTP:
        attempts = 0

        def __init__(self, host: str, port: int, timeout: int) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            FakeSMTP.attempts += 1
            if FakeSMTP.attempts < 3:
                raise OSError("temporary")
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            return None

        def send_message(self, message) -> dict:
            return {}

    monkeypatch.setattr("findmejobs.delivery.email.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("findmejobs.delivery.email.time.sleep", lambda _seconds: None)

    example_app = Path(__file__).resolve().parents[1] / "config" / "examples" / "app.toml"
    sender = SMTPEmailSender(
        load_app_config(example_app).delivery.email.model_copy(
            update={"enabled": True, "host": "smtp.example.test", "sender": "from@example.test", "recipient": "to@example.test"}
        )
    )
    result = sender.send(subject="digest", body_text="body")
    assert result.attempts == 3
    assert sender.last_attempt_count == 3


def test_smtp_sender_raises_after_final_retry(monkeypatch) -> None:
    class FailingSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            pass

        def __enter__(self):
            raise OSError("still down")

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr("findmejobs.delivery.email.smtplib.SMTP", FailingSMTP)
    monkeypatch.setattr("findmejobs.delivery.email.time.sleep", lambda _seconds: None)

    example_app = Path(__file__).resolve().parents[1] / "config" / "examples" / "app.toml"
    sender = SMTPEmailSender(
        load_app_config(example_app).delivery.email.model_copy(
            update={"enabled": True, "host": "smtp.example.test", "sender": "from@example.test", "recipient": "to@example.test"}
        )
    )
    with pytest.raises(OSError, match="still down"):
        sender.send(subject="digest", body_text="body")
    assert sender.last_attempt_count == 3


def test_feedback_storage_and_matching_are_explicit(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-1",
            source_id="source-1",
            source_name="lever-source",
            source_kind="lever",
            normalized_id="normalized-1",
            source_job_id="source-job-1",
            source_job_key="job-1",
            title="Backend Engineer",
            company="Example Inc.",
            location_text="Remote, Philippines",
        )
        with pytest.raises(ValueError, match="invalid_feedback_type"):
            record_feedback(session, id_factory=new_id, feedback_type="maybe")
        record_feedback(session, id_factory=new_id, feedback_type="blocked_company", cluster_id="cluster-1")
        record_feedback(session, id_factory=new_id, feedback_type="blocked_title", title_keyword="backend")
        record_feedback(session, id_factory=new_id, feedback_type="relevant", cluster_id="cluster-1")
        session.commit()
        feedback_types = feedback_types_for_job(
            session,
            cluster_id="cluster-1",
            company_name="Example, Inc.",
            title="Senior Backend Engineer",
        )
    assert feedback_types == ["blocked_company", "blocked_title", "relevant"]


def test_ranking_inputs_cover_title_family_company_timezone_remote_source_trust_and_feedback(runtime_config_files) -> None:
    _, profile_path, _ = runtime_config_files
    profile = load_profile_config(profile_path)
    profile.target_titles = ["Backend Engineer"]
    profile.ranking.title_families = {"Backend Engineer": ["Software Engineer"]}
    profile.ranking.preferred_companies = ["Example"]
    profile.ranking.allowed_companies = ["Example"]
    profile.ranking.preferred_timezones = ["Asia/Manila"]
    profile.ranking.remote_first = True
    job = _job(title="Software Engineer", company_name="Example", source_trust_weight=1.8)

    first = rank_job_with_feedback(job, profile, feedback_types=["relevant"])
    second = rank_job_with_feedback(job, profile, feedback_types=["relevant"])
    blocked = rank_job_with_feedback(_job(company_name="Wrong Co"), profile, feedback_types=[])
    hybrid = rank_job_with_feedback(_job(location_type="hybrid"), profile, feedback_types=[])
    stale = rank_job_with_feedback(_job(posted_at=utcnow() - timedelta(days=29)), profile, feedback_types=[])

    assert first.total == second.total
    assert first.components["title_family"] > 0
    assert first.components["company_preference"] > 0
    assert first.components["timezone_fit"] > 0
    assert first.components["source_trust"] > 0
    assert first.components["feedback_signal"] > 0
    assert "company_not_allowed" in blocked.hard_filter_reasons
    assert first.components["remote_fit"] > hybrid.components["remote_fit"]
    assert first.components["recency"] > stale.components["recency"]


def test_doctor_surfaces_delivery_failures_and_reportable_runtime_issues(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    required_paths = [
        app_config.storage.root_dir,
        app_config.storage.raw_dir,
        app_config.storage.review_outbox_dir,
        app_config.storage.review_inbox_dir,
        app_config.storage.lock_dir,
    ]
    with session_factory() as session:
        now = utcnow()
        source = Source(
            id="source-1",
            name="source-1",
            kind="lever",
            enabled=True,
            priority=10,
            trust_weight=1.0,
            fetch_cap=20,
            config_json={},
            created_at=now,
            updated_at=now,
            last_successful_run_at=now,
        )
        session.add(source)
        session.add(
            PipelineRun(
                id="run-1",
                command="rank",
                started_at=now,
                finished_at=now,
                status="success",
                stats_json={},
            )
        )
        digest = Digest(
            id="digest-1",
            channel=app_config.delivery.channel,
            digest_date="2026-03-19",
            window_start=now - timedelta(days=1),
            window_end=now,
            status="failed",
            subject="failed",
            body_text="failed",
        )
        session.add(digest)
        session.flush()
        for idx in range(4):
            session.add(
                DeliveryEvent(
                    id=f"event-{idx}",
                    digest_id=digest.id,
                    channel=app_config.delivery.channel,
                    status="failed",
                    attempt=idx + 1,
                    error_message="smtp_down",
                    metadata_json={},
                    created_at=now,
                )
            )
        session.commit()
        errors = run_doctor(session, app_config.database.url, required_paths)
    assert "latest_digest_failed" in errors
    assert "delivery_failures_high" in errors


def test_slice2_migration_preserves_slice1_rows(tmp_path: Path, project_root: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0001_slice1")

    session_factory = create_session_factory(database_url)
    with session_factory() as session:
        now = utcnow()
        session.execute(
            text(
                "INSERT INTO sources (id, name, kind, enabled, config_json, created_at, updated_at) "
                "VALUES (:id, :name, :kind, :enabled, :config_json, :created_at, :updated_at)"
            ),
            {
                "id": "source-1",
                "name": "legacy-source",
                "kind": "rss",
                "enabled": 1,
                "config_json": "{}",
                "created_at": now,
                "updated_at": now,
            },
        )
        session.commit()

    command.upgrade(config, "head")
    session_factory = create_session_factory(database_url)
    with session_factory() as session:
        source = session.get(Source, "source-1")
        assert source is not None
        assert source.name == "legacy-source"
        assert session.scalar(select(func.count()).select_from(JobFeedback)) == 0
        assert session.scalar(select(func.count()).select_from(DeliveryEvent)) == 0


def test_feedback_query_only_fetches_relevant_rows(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    """Verify that feedback_types_for_job returns correct results using filtered query."""
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-a",
            source_id="source-a",
            source_name="lever-a",
            source_kind="lever",
            normalized_id="norm-a",
            source_job_id="sj-a",
            source_job_key="job-a",
            title="Backend Engineer",
            company="Acme Corp",
            location_text="Remote, Philippines",
        )
        _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-b",
            source_id="source-b",
            source_name="lever-b",
            source_kind="lever",
            normalized_id="norm-b",
            source_job_id="sj-b",
            source_job_key="job-b",
            title="Frontend Engineer",
            company="Other Inc",
            location_text="Manila",
        )
        record_feedback(session, id_factory=new_id, feedback_type="relevant", cluster_id="cluster-a")
        record_feedback(session, id_factory=new_id, feedback_type="irrelevant", cluster_id="cluster-b")
        record_feedback(session, id_factory=new_id, feedback_type="blocked_company", company_name="Bad Co")
        session.commit()

        types_a = feedback_types_for_job(session, cluster_id="cluster-a", company_name="Acme Corp", title="Backend Engineer")
        assert "relevant" in types_a
        assert "irrelevant" not in types_a
        assert "blocked_company" not in types_a

        types_b = feedback_types_for_job(session, cluster_id="cluster-b", company_name="Other Inc", title="Frontend Engineer")
        assert "irrelevant" in types_b
        assert "relevant" not in types_b

        types_bad = feedback_types_for_job(session, cluster_id="cluster-x", company_name="Bad Co", title="Backend Engineer")
        assert "blocked_company" in types_bad
        assert "relevant" not in types_bad


def test_send_digest_dry_run_does_not_send_email(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        _seed_reviewable_cluster(
            session,
            profile,
            cluster_id="cluster-dry",
            source_id="source-dry",
            source_name="lever-dry",
            source_kind="lever",
            normalized_id="norm-dry",
            source_job_id="sj-dry",
            source_job_key="job-dry",
            title="Backend Engineer",
            company="DryRun Corp",
            location_text="Remote, Philippines",
        )
        session.commit()

        class BoomSender:
            def send(self, *, subject: str, body_text: str) -> str:
                raise AssertionError("should not be called in dry_run mode")

        digest = send_digest(
            session,
            app_config,
            profile,
            id_factory=new_id,
            sender=BoomSender(),
            digest_date="2026-03-20",
            dry_run=True,
        )
        session.commit()

        assert digest.status == "dry_run"
        assert digest.sent_at is None
        assert session.scalar(select(func.count()).select_from(DeliveryEvent).where(DeliveryEvent.digest_id == digest.id)) == 0


def test_ph_board_observability_migration_adds_fetch_run_counters(tmp_path: Path, project_root: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'ph-board-observability.db'}"
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0002_slice2")
    command.upgrade(config, "head")

    session_factory = create_session_factory(database_url)
    with session_factory() as session:
        columns = {row[1] for row in session.execute(text("PRAGMA table_info(source_fetch_runs)")).all()}

    assert "raw_seen_count" in columns
    assert "skipped_count" in columns
