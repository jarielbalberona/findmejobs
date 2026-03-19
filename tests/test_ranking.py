from __future__ import annotations

from datetime import timedelta

from findmejobs.config.loader import load_profile_config
from findmejobs.db.models import JobCluster, NormalizedJob, RawDocument, Source, SourceFetchRun, SourceJob
from findmejobs.db.repositories import upsert_job_score, upsert_profile, upsert_rank_model
from findmejobs.domain.job import CanonicalJob
from findmejobs.ranking.engine import rank_job
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
        "description_text": "Python SQL AWS FastAPI",
        "tags": ["python", "sql", "aws", "fastapi"],
        "first_seen_at": now,
        "last_seen_at": now,
        "posted_at": now - timedelta(days=2),
    }
    payload.update(overrides)
    return CanonicalJob(**payload)


def _seed_cluster(session, cluster_id: str, now) -> None:
    source = Source(
        id="source-1",
        name="source-1",
        kind="rss",
        enabled=True,
        config_json={},
        created_at=now,
        updated_at=now,
    )
    fetch_run = SourceFetchRun(
        id="run-1",
        source_id=source.id,
        started_at=now,
        status="success",
        attempt_count=1,
        item_count=1,
    )
    raw_document = RawDocument(
        id="raw-1",
        source_id=source.id,
        fetch_run_id=fetch_run.id,
        url="https://example.test/feed",
        canonical_url="https://example.test/feed",
        content_type="application/rss+xml",
        http_status=200,
        sha256="raw-1",
        storage_path="/tmp/raw-1.xml",
        fetched_at=now,
    )
    source_job = SourceJob(
        id="source-job-1",
        source_id=source.id,
        raw_document_id=raw_document.id,
        fetch_run_id=fetch_run.id,
        source_job_key="job-1",
        source_url="https://example.test/jobs/1",
        apply_url="https://example.test/jobs/1",
        payload_json={},
        seen_at=now,
    )
    normalized = NormalizedJob(
        id="normalized-1",
        source_job_id=source_job.id,
        canonical_url="https://example.test/jobs/1",
        company_name="Example",
        title="Backend Engineer",
        location_text="Remote, Philippines",
        location_type="remote",
        country_code="PH",
        description_text="Python SQL AWS FastAPI",
        description_sha256="desc-1",
        tags_json=["python", "sql", "aws", "fastapi"],
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
    session.add(raw_document)
    session.flush()
    session.add(source_job)
    session.flush()
    session.add(normalized)
    session.flush()
    session.add(cluster)


def test_hard_filters_reject_obvious_mismatches(runtime_config_files) -> None:
    _, profile_path, _ = runtime_config_files
    profile = load_profile_config(profile_path)

    blocked = _job(company_name="Reject Co")
    onsite = _job(location_type="onsite", location_text="Manila")

    assert "blocked_company" in rank_job(blocked, profile).hard_filter_reasons
    assert "not_remote" in rank_job(onsite, profile).hard_filter_reasons


def test_weighted_scoring_is_deterministic_and_preserves_breakdown(runtime_config_files, session_factory) -> None:
    _, profile_path, _ = runtime_config_files
    profile = load_profile_config(profile_path)
    job = _job()
    first = rank_job(job, profile)
    second = rank_job(job, profile)

    assert first.total == second.total
    assert first.components == second.components

    with session_factory() as session:
        _seed_cluster(session, "cluster-1", utcnow())
        profile_row = upsert_profile(session, profile, new_id)
        rank_model = upsert_rank_model(session, profile, new_id)
        score = upsert_job_score(session, "cluster-1", profile_row.id, rank_model.id, first, new_id)
        session.commit()
        assert score.score_breakdown_json == first.components


def test_stale_jobs_are_penalized(runtime_config_files) -> None:
    _, profile_path, _ = runtime_config_files
    profile = load_profile_config(profile_path)
    fresh = rank_job(_job(posted_at=utcnow() - timedelta(days=1)), profile)
    stale = rank_job(_job(posted_at=utcnow() - timedelta(days=29)), profile)
    assert fresh.components["recency"] > stale.components["recency"]


def test_ranking_has_no_llm_dependency(runtime_config_files) -> None:
    _, profile_path, _ = runtime_config_files
    profile = load_profile_config(profile_path)
    assert isinstance(rank_job(_job(), profile).total, float)
