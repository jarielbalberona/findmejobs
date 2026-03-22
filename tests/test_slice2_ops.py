from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from findmejobs.cli.app import app
from findmejobs.config.loader import load_app_config
from findmejobs.db.models import DeliveryEvent, Digest, JobCluster, JobScore, NormalizedJob, OpenClawReview, RawDocument, ReviewPacket, Source, SourceFetchRun, SourceJob
from findmejobs.db.repositories import upsert_job_score, upsert_profile, upsert_rank_model
from findmejobs.db.session import create_session_factory
from findmejobs.domain.ranking import ScoreBreakdown
from findmejobs.feedback import feedback_types_for_job
from findmejobs.observability.reporting import build_report
from findmejobs.ranking.engine import rank_job_with_feedback
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow


def _seed_ranked_reviewable_job(session, *, source_name: str = "lever-source", source_kind: str = "lever") -> tuple[str, str]:
    now = utcnow()
    source = Source(
        id="source-1",
        name=source_name,
        kind=source_kind,
        enabled=True,
        priority=10,
        trust_weight=1.4,
        fetch_cap=50,
        config_json={},
        created_at=now,
        updated_at=now,
        last_successful_run_at=now,
    )
    fetch_run = SourceFetchRun(
        id="fetch-1",
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
        id="raw-1",
        source_id=source.id,
        fetch_run_id=fetch_run.id,
        url="https://jobs.example.test/1",
        canonical_url="https://jobs.example.test/1",
        content_type="application/json",
        http_status=200,
        sha256="raw-1",
        storage_path="/tmp/raw-1.json",
        fetched_at=now,
    )
    source_job = SourceJob(
        id="source-job-1",
        source_id=source.id,
        raw_document_id=raw.id,
        fetch_run_id=fetch_run.id,
        source_job_key="job-1",
        source_url="https://jobs.example.test/1",
        apply_url="https://jobs.example.test/1",
        payload_json={
            "title": "Backend Engineer",
            "company": "Example",
            "location_text": "Remote, Philippines",
            "description": "Python SQL AWS",
            "posted_at": now.isoformat(),
        },
        seen_at=now,
    )
    normalized = NormalizedJob(
        id="normalized-1",
        source_job_id=source_job.id,
        canonical_url="https://jobs.example.test/1",
        company_name="Example",
        title="Backend Engineer",
        location_text="Remote, Philippines",
        location_type="remote",
        country_code="PH",
        description_text="Python SQL AWS",
        description_sha256="desc-1",
        tags_json=["python", "sql", "aws"],
        posted_at=now,
        first_seen_at=now,
        last_seen_at=now,
        normalization_status="valid",
        normalization_errors_json=[],
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
    cluster = JobCluster(
        id="cluster-1",
        cluster_key="cluster-1",
        representative_job_id=normalized.id,
        created_at=now,
        updated_at=now,
    )
    session.add(cluster)
    session.flush()
    return cluster.id, normalized.id


def test_feedback_blocked_company_affects_rerank(migrated_runtime_config_files: tuple[Path, Path, Path], cli_runner) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        cluster_id, _ = _seed_ranked_reviewable_job(session)
        from findmejobs.config.loader import load_profile_config

        profile = load_profile_config(profile_path)
        profile_row = upsert_profile(session, profile, new_id)
        rank_model = upsert_rank_model(session, profile, new_id)
        breakdown = ScoreBreakdown(components={"title_alignment": 10.0})
        upsert_job_score(session, cluster_id, profile_row.id, rank_model.id, breakdown, new_id)
        session.commit()

    record = cli_runner.invoke(
        app,
        [
            "feedback",
            "record",
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
            "--feedback-type",
            "blocked_company",
            "--cluster-id",
            "cluster-1",
        ],
    )
    assert record.exit_code == 0

    rerank = cli_runner.invoke(
        app,
        ["rerank", "--app-config-path", str(app_path), "--profile-path", str(profile_path), "--sources-dir", str(sources_dir)],
    )
    assert rerank.exit_code == 0
    with session_factory() as session:
        score = session.scalar(select(JobScore).where(JobScore.cluster_id == "cluster-1"))
        assert "feedback_blocked_company" in score.hard_filter_reasons_json


def test_digest_send_records_delivery_event(migrated_runtime_config_files: tuple[Path, Path, Path], cli_runner, monkeypatch) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        cluster_id, _ = _seed_ranked_reviewable_job(session)
        from findmejobs.config.loader import load_profile_config

        profile = load_profile_config(profile_path)
        profile_row = upsert_profile(session, profile, new_id)
        rank_model = upsert_rank_model(session, profile, new_id)
        breakdown = rank_job_with_feedback(
            __import__("findmejobs.cli.app", fromlist=["_canonical_job_from_row"])._canonical_job_from_row(
                session.get(NormalizedJob, "normalized-1"), session.get(Source, "source-1")
            ),
            profile,
            feedback_types=[],
        )
        score = upsert_job_score(session, cluster_id, profile_row.id, rank_model.id, breakdown, new_id)
        packet = ReviewPacket(
            id="packet-1",
            cluster_id=cluster_id,
            job_score_id=score.id,
            packet_version="v1",
            packet_json={"packet_id": "packet-1"},
            packet_sha256="packet-sha",
            status="exported",
            built_at=utcnow(),
            exported_at=utcnow(),
        )
        review = OpenClawReview(
            id="review-1",
            review_packet_id=packet.id,
            provider_review_id="provider-1",
            decision="keep",
            confidence_label="high",
            reasons_json=["good fit"],
            draft_summary="good fit",
            draft_actions_json=["apply"],
            raw_response_json={},
            reviewed_at=utcnow(),
            imported_at=utcnow(),
        )
        session.add(packet)
        session.flush()
        session.add(review)
        session.commit()

    class FakeSender:
        def __init__(self, config) -> None:
            self.config = config

        def send(self, *, subject: str, body_text: str) -> str:
            assert "Backend Engineer" in body_text
            return "provider-123"

    monkeypatch.setattr("findmejobs.delivery.digest.SMTPEmailSender", FakeSender)
    result = cli_runner.invoke(
        app,
        ["digest", "send", "--app-config-path", str(app_path), "--profile-path", str(profile_path), "--sources-dir", str(sources_dir)],
    )
    assert result.exit_code == 0
    with session_factory() as session:
        assert session.scalar(select(Digest)) is not None
        event = session.scalar(select(DeliveryEvent))
        assert event is not None
        assert event.status == "sent"


def test_report_command_surfaces_source_health(migrated_runtime_config_files: tuple[Path, Path, Path], cli_runner) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        _seed_ranked_reviewable_job(session, source_name="smart-source", source_kind="smartrecruiters")
        session.commit()

    result = cli_runner.invoke(
        app,
        ["report", "--app-config-path", str(app_path), "--profile-path", str(profile_path), "--sources-dir", str(sources_dir)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["sources"]
    assert "quality_gates" in payload
    assert "application_funnel" in payload
    assert "max_parse_error_rate" in payload["quality_gates"]
    smart = next(item for item in payload["sources"] if item["name"] == "smart-source")
    assert smart["latest_status"] == "success"
    assert smart["family"] == "predictable_ats"


def test_reprocess_normalize_updates_selected_record(migrated_runtime_config_files: tuple[Path, Path, Path], cli_runner) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        _cluster_id, normalized_id = _seed_ranked_reviewable_job(session)
        source_job = session.get(SourceJob, "source-job-1")
        source_job.payload_json = {**source_job.payload_json, "description": "Python SQL AWS Kubernetes"}
        session.commit()

    result = cli_runner.invoke(
        app,
        [
            "reprocess",
            "normalize",
            "--app-config-path",
            str(app_path),
            "--profile-path",
            str(profile_path),
            "--sources-dir",
            str(sources_dir),
            "--source-job-id",
            "source-job-1",
        ],
    )
    assert result.exit_code == 0
    with session_factory() as session:
        normalized = session.get(NormalizedJob, normalized_id)
        assert "Kubernetes" in normalized.description_text


def test_build_report_function_counts_ranked_vs_filtered(migrated_runtime_config_files: tuple[Path, Path, Path]) -> None:
    app_path, profile_path, _sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    session_factory = create_session_factory(app_config.database.url)
    with session_factory() as session:
        cluster_id, _ = _seed_ranked_reviewable_job(session)
        from findmejobs.config.loader import load_profile_config

        profile = load_profile_config(profile_path)
        profile_row = upsert_profile(session, profile, new_id)
        rank_model = upsert_rank_model(session, profile, new_id)
        upsert_job_score(session, cluster_id, profile_row.id, rank_model.id, ScoreBreakdown(components={"title_alignment": 1.0}), new_id)
        session.commit()
        report = build_report(session)
    assert report["ranking"]["ranked"] >= 1
    assert report["application_funnel"]["ready_count"] >= 1
