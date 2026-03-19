from __future__ import annotations

from datetime import datetime

import pytest

from findmejobs.domain.source import SourceJobRecord
from findmejobs.ingestion.adapters.rss import canonical_rss_key
from findmejobs.normalization.canonicalize import normalize_job
from findmejobs.utils.time import utcnow


@pytest.mark.parametrize(
    ("location_text", "description_text", "expected"),
    [
        ("Remote, Philippines", "Python", "remote"),
        ("Manila", "Hybrid twice a week", "hybrid"),
        ("Manila", "Office role", "onsite"),
        ("", "No location stated", "unknown"),
    ],
)
def test_location_normalization(location_text: str, description_text: str, expected: str) -> None:
    record = SourceJobRecord(
        source_job_key="job-1",
        source_url="https://example.test/jobs/1",
        apply_url="https://example.test/jobs/1",
        title="Backend Engineer",
        company="Example",
        location_text=location_text,
        description_raw=description_text,
        raw_payload={},
    )
    job = normalize_job("source-job-id", "source-id", utcnow(), record)
    assert job.location_type == expected


def test_rss_and_ats_records_map_to_canonical_job_model() -> None:
    seen_at = utcnow()
    rss_record = SourceJobRecord(
        source_job_key=canonical_rss_key("https://example.test/jobs/1", "Backend Engineer"),
        source_url="https://example.test/jobs/1?utm_source=rss",
        apply_url="https://example.test/jobs/1?utm_source=rss",
        title="Backend Engineer",
        company="Example Labs",
        location_text="Remote, Philippines",
        posted_at_raw="Wed, 19 Mar 2026 08:00:00 GMT",
        description_raw="<p>Python SQL AWS</p>",
        tags_raw=["Backend"],
        raw_payload={},
    )
    ats_record = SourceJobRecord(
        source_job_key="101",
        source_url="https://boards.greenhouse.io/acme/jobs/101?gh_jid=101&utm_source=boards",
        apply_url="https://boards.greenhouse.io/acme/jobs/101?gh_jid=101&utm_source=boards",
        title="Backend Engineer",
        company="Acme",
        location_text="Remote, Philippines",
        posted_at_raw="2026-03-19T07:00:00Z",
        description_raw="<div><p>Python SQL AWS</p></div>",
        raw_payload={},
    )
    rss_job = normalize_job("rss-job", "rss-source", seen_at, rss_record)
    ats_job = normalize_job("ats-job", "ats-source", seen_at, ats_record)

    assert rss_job.canonical_url == "https://example.test/jobs/1"
    assert ats_job.canonical_url == "https://boards.greenhouse.io/acme/jobs/101?gh_jid=101"
    assert rss_job.country_code == "PH"
    assert ats_job.location_type == "remote"
    assert "python" in ats_job.tags


def test_malformed_fields_are_handled_safely() -> None:
    record = SourceJobRecord(
        source_job_key="bad",
        source_url="not-a-url",
        apply_url="not-a-url",
        title="Platform Engineer",
        company="Bad URL Inc",
        location_text="unknown",
        posted_at_raw="not-a-date",
        salary_raw="competitive",
        description_raw="<p>Role text</p>",
        raw_payload={},
    )
    job = normalize_job("bad-id", "source-id", utcnow(), record)

    assert "invalid_url" in job.normalization_errors
    assert job.posted_at is None
    assert job.salary_min is None
    assert job.salary_max is None


def test_source_job_key_logic_is_stable() -> None:
    assert canonical_rss_key("https://example.test/jobs/1", "Role") == canonical_rss_key("https://example.test/jobs/1", "Role")
