from __future__ import annotations

from sqlalchemy import select, text

from findmejobs.db.models import JobCluster, JobClusterMember, NormalizedJob, RawDocument, Source, SourceFetchRun, SourceJob
from findmejobs.dedupe.clustering import assign_job_cluster
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow


def _make_normalized_job(session, *, source_id: str, source_job_key: str, source_url: str, canonical_url: str | None, company: str, title: str, location: str, now):
    source = Source(
        id=source_id,
        name=source_id,
        kind="rss",
        enabled=True,
        config_json={},
        created_at=now,
        updated_at=now,
    )
    fetch_run = SourceFetchRun(
        id=f"run-{source_id}",
        source_id=source.id,
        started_at=now,
        status="success",
        attempt_count=1,
        item_count=1,
    )
    raw = RawDocument(
        id=f"raw-{source_id}",
        source_id=source.id,
        fetch_run_id=fetch_run.id,
        url=source_url,
        canonical_url=source_url,
        content_type="application/json",
        http_status=200,
        sha256=f"sha-{source_id}",
        storage_path=f"/tmp/{source_id}.json",
        fetched_at=now,
    )
    source_job = SourceJob(
        id=new_id(),
        source_id=source.id,
        raw_document_id=raw.id,
        fetch_run_id=fetch_run.id,
        source_job_key=source_job_key,
        source_url=source_url,
        apply_url=canonical_url,
        payload_json={},
        seen_at=now,
    )
    normalized = NormalizedJob(
        id=new_id(),
        source_job_id=source_job.id,
        canonical_url=canonical_url,
        company_name=company,
        title=title,
        location_text=location,
        location_type="remote" if "remote" in location.casefold() else "onsite",
        description_text="Python SQL",
        description_sha256=new_id(),
        tags_json=["python"],
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
    return normalized


def test_exact_canonical_url_duplicates_collapse(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        job_a = _make_normalized_job(
            session,
            source_id="source-a",
            source_job_key="a",
            source_url="https://mirror-a.test/jobs/1",
            canonical_url="https://jobs.example.test/jobs/1",
            company="Example",
            title="Backend Engineer",
            location="Remote, Philippines",
            now=now,
        )
        job_b = _make_normalized_job(
            session,
            source_id="source-b",
            source_job_key="b",
            source_url="https://mirror-b.test/jobs/1",
            canonical_url="https://jobs.example.test/jobs/1",
            company="Example",
            title="Backend Engineer",
            location="Remote, Philippines",
            now=now,
        )
        cluster_a, _ = assign_job_cluster(session, job_a, new_id)
        cluster_b, _ = assign_job_cluster(session, job_b, new_id)
        session.commit()
        assert cluster_a.id == cluster_b.id


def test_source_job_key_does_not_merge_across_sources(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        job_a = _make_normalized_job(session, source_id="a", source_job_key="shared", source_url="https://a.test/1", canonical_url=None, company="Example", title="Backend Engineer", location="Remote", now=now)
        job_b = _make_normalized_job(session, source_id="b", source_job_key="shared", source_url="https://b.test/1", canonical_url=None, company="Different", title="Different title", location="Onsite", now=now)
        cluster_a, _ = assign_job_cluster(session, job_a, new_id)
        cluster_b, _ = assign_job_cluster(session, job_b, new_id)
        session.commit()
        assert cluster_a.id != cluster_b.id


def test_normalized_company_title_location_duplicates_are_detected(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        job_a = _make_normalized_job(session, source_id="a", source_job_key="1", source_url="https://a.test/1", canonical_url=None, company="Example Inc.", title="Backend Engineer", location="Remote Philippines", now=now)
        job_b = _make_normalized_job(session, source_id="b", source_job_key="2", source_url="https://b.test/2", canonical_url=None, company="example", title=" backend engineer ", location=" remote philippines ", now=now)
        cluster_a, _ = assign_job_cluster(session, job_a, new_id)
        cluster_b, _ = assign_job_cluster(session, job_b, new_id)
        session.commit()
        assert cluster_a.id == cluster_b.id


def test_unrelated_jobs_do_not_false_positive_into_same_cluster(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        job_a = _make_normalized_job(session, source_id="a", source_job_key="1", source_url="https://a.test/1", canonical_url=None, company="Example", title="Backend Engineer", location="Remote", now=now)
        job_b = _make_normalized_job(session, source_id="b", source_job_key="2", source_url="https://b.test/2", canonical_url=None, company="Another", title="Data Analyst", location="Manila", now=now)
        cluster_a, _ = assign_job_cluster(session, job_a, new_id)
        cluster_b, _ = assign_job_cluster(session, job_b, new_id)
        session.commit()
        assert cluster_a.id != cluster_b.id


def test_cluster_merge_does_not_fail_when_last_seen_mixes_naive_and_aware(session_factory) -> None:
    """Regression: choose_representative_job sorted() compared naive vs aware datetimes."""
    now = utcnow()
    canonical = "https://jobs.example.test/dedupe-mixed-tz/1"
    with session_factory() as session:
        job_a = _make_normalized_job(
            session,
            source_id="source-a",
            source_job_key="a",
            source_url="https://mirror-a.test/jobs/1",
            canonical_url=canonical,
            company="Example",
            title="Backend Engineer",
            location="Remote, Philippines",
            now=now,
        )
        assign_job_cluster(session, job_a, new_id)
        session.commit()
        session.execute(
            text("UPDATE normalized_jobs SET last_seen_at = :v WHERE id = :id"),
            {"v": "2020-01-01 12:00:00.000000", "id": job_a.id},
        )
        session.commit()

        job_b = _make_normalized_job(
            session,
            source_id="source-b",
            source_job_key="b",
            source_url="https://mirror-b.test/jobs/1",
            canonical_url=canonical,
            company="Example",
            title="Backend Engineer",
            location="Remote, Philippines",
            now=now,
        )
        _cluster, merged = assign_job_cluster(session, job_b, new_id)
        session.commit()
        assert merged is True
