from __future__ import annotations

from datetime import datetime

import pytest

from findmejobs.domain.source import SourceJobRecord
from findmejobs.ingestion.adapters.rss import canonical_rss_key
from findmejobs.normalization.canonicalize import normalize_job
from findmejobs.review.packets import sanitize_review_text
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
        source_company_id="acme",
        title="Backend Engineer",
        company="Acme",
        location_text="Remote, Philippines",
        posted_at_raw="2026-03-19T07:00:00Z",
        description_raw="<div><p>Python SQL AWS</p></div>",
        raw_payload={},
    )
    rss_job = normalize_job("rss-job", "rss-source", seen_at, rss_record)
    ats_job = normalize_job(
        "ats-job",
        "ats-source",
        seen_at,
        ats_record,
        source_name="acme-greenhouse",
        source_kind="greenhouse",
        source_priority=20,
        source_trust_weight=1.1,
    )

    assert rss_job.canonical_url == "https://example.test/jobs/1"
    assert ats_job.canonical_url == "https://boards.greenhouse.io/acme/jobs/101?gh_jid=101"
    assert rss_job.country_code == "PH"
    assert ats_job.location_type == "remote"
    assert ats_job.source_name == "acme-greenhouse"
    assert ats_job.source_kind == "greenhouse"
    assert ats_job.source_company_id == "acme"
    assert ats_job.job_url == ats_record.source_url
    assert ats_job.apply_url == ats_record.apply_url
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


def test_normalization_decodes_and_strips_escaped_html_markup() -> None:
    record = SourceJobRecord(
        source_job_key="escaped-html",
        source_url="https://example.test/jobs/escaped-html",
        apply_url="https://example.test/jobs/escaped-html",
        title="Backend Engineer",
        company="Example",
        location_text="Remote, Philippines",
        description_raw="&lt;p&gt;Python &lt;strong&gt;SQL&lt;/strong&gt; &amp; AWS&lt;/p&gt;",
        raw_payload={},
    )

    job = normalize_job("source-job-id", "source-id", utcnow(), record)

    assert job.description_text == "Python SQL & AWS"
    assert sanitize_review_text(job.description_text) == "Python SQL & AWS"


def test_normalization_removes_escaped_hostile_tags_before_review_boundary() -> None:
    record = SourceJobRecord(
        source_job_key="escaped-script",
        source_url="https://example.test/jobs/escaped-script",
        apply_url="https://example.test/jobs/escaped-script",
        title="Backend Engineer",
        company="Example",
        location_text="Remote, Philippines",
        description_raw="Plain text &lt;script&gt;bad&lt;/script&gt; Python SQL",
        raw_payload={},
    )

    job = normalize_job("source-job-id", "source-id", utcnow(), record)

    assert "script" not in job.description_text.casefold()
    assert sanitize_review_text(job.description_text) == "Plain text Python SQL"


@pytest.mark.parametrize(
    ("salary_text", "expected_min", "expected_max", "expected_currency", "expected_period"),
    [
        ("$100,000 - $150,000 per year", 100000, 150000, "USD", "year"),
        ("100k - 150k", 100000, 150000, None, None),
        ("$120K", 120000, 120000, "USD", None),
        ("$45/hr", 45, 45, "USD", "hour"),
        ("£60k per year", 60000, 60000, "GBP", "year"),
        ("€50K - €70K per annum", 50000, 70000, "EUR", "year"),
        ("PHP 90,000 - 120,000 / month", 90000, 120000, "PHP", "month"),
        ("₱80,000 per month", 80000, 80000, "PHP", "month"),
        ("competitive", None, None, None, None),
        ("", None, None, None, None),
        (None, None, None, None, None),
    ],
)
def test_parse_salary_handles_various_formats(
    salary_text: str | None,
    expected_min: int | None,
    expected_max: int | None,
    expected_currency: str | None,
    expected_period: str | None,
) -> None:
    from findmejobs.normalization.canonicalize import parse_salary

    sal_min, sal_max, currency, period = parse_salary(salary_text)
    assert sal_min == expected_min, f"min: {sal_min} != {expected_min} for {salary_text!r}"
    assert sal_max == expected_max, f"max: {sal_max} != {expected_max} for {salary_text!r}"
    assert currency == expected_currency, f"currency: {currency} != {expected_currency} for {salary_text!r}"
    assert period == expected_period, f"period: {period} != {expected_period} for {salary_text!r}"


def test_source_job_key_logic_is_stable() -> None:
    assert canonical_rss_key("https://example.test/jobs/1", "Role") == canonical_rss_key("https://example.test/jobs/1", "Role")
