from __future__ import annotations

import re

from findmejobs.domain.job import CanonicalJob
from findmejobs.domain.source import SourceJobRecord
from findmejobs.utils.hashing import sha256_hexdigest
from findmejobs.utils.text import collapse_whitespace, html_to_text
from findmejobs.utils.time import parse_datetime
from findmejobs.utils.urls import canonicalize_url

SENIORITY_MAP = {
    "senior": "senior",
    "staff": "staff",
    "lead": "lead",
    "principal": "principal",
    "junior": "junior",
    "intern": "intern",
}

EMPLOYMENT_MAP = {
    "full-time": "full_time",
    "full time": "full_time",
    "contract": "contract",
    "part-time": "part_time",
    "part time": "part_time",
}

COUNTRY_HINTS = {
    "united states": "US",
    "usa": "US",
    "canada": "CA",
    "philippines": "PH",
    "united kingdom": "GB",
    "uk": "GB",
}


def normalize_job(source_job_id: str, source_id: str, seen_at, record: SourceJobRecord) -> CanonicalJob:
    errors: list[str] = []
    canonical_url = canonicalize_url(record.apply_url or record.source_url)
    if canonical_url is None:
        errors.append("invalid_url")
    title = collapse_whitespace(record.title)
    company = collapse_whitespace(record.company) or "Unknown"
    description_text = html_to_text(record.description_raw or "")
    location_text = collapse_whitespace(record.location_text)
    location_type = infer_location_type(location_text, description_text)
    country_code = infer_country_code(location_text)
    posted_at = parse_datetime(record.posted_at_raw)
    seniority = infer_seniority(record.seniority_raw or title)
    employment_type = infer_employment_type(record.employment_type_raw or description_text)
    tags = normalize_tags(record.tags_raw, title, description_text)
    salary_min, salary_max, salary_currency, salary_period = parse_salary(record.salary_raw or description_text)
    return CanonicalJob(
        source_job_id=source_job_id,
        source_id=source_id,
        source_job_key=record.source_job_key,
        canonical_url=canonical_url,
        company_name=company,
        title=title,
        location_text=location_text,
        location_type=location_type,
        country_code=country_code,
        seniority=seniority,
        employment_type=employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        salary_period=salary_period,
        description_text=description_text,
        tags=tags,
        posted_at=posted_at,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        normalization_errors=errors,
    )


def infer_location_type(location_text: str, description_text: str) -> str:
    haystack = f"{location_text} {description_text}".casefold()
    if "remote" in haystack:
        return "remote"
    if "hybrid" in haystack:
        return "hybrid"
    if location_text:
        return "onsite"
    return "unknown"


def infer_country_code(location_text: str) -> str | None:
    haystack = location_text.casefold()
    for fragment, code in COUNTRY_HINTS.items():
        if fragment in haystack:
            return code
    return None


def infer_seniority(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.casefold()
    for fragment, normalized in SENIORITY_MAP.items():
        if fragment in lowered:
            return normalized
    return None


def infer_employment_type(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.casefold()
    for fragment, normalized in EMPLOYMENT_MAP.items():
        if fragment in lowered:
            return normalized
    return None


def normalize_tags(tags_raw: list[str], title: str, description_text: str) -> list[str]:
    tags = {collapse_whitespace(tag).casefold() for tag in tags_raw if collapse_whitespace(tag)}
    skill_candidates = re.findall(r"\b(python|django|fastapi|sql|aws|kubernetes|react)\b", f"{title} {description_text}".casefold())
    tags.update(skill_candidates)
    return sorted(tags)


def parse_salary(value: str | None) -> tuple[int | None, int | None, str | None, str | None]:
    if not value:
        return None, None, None, None
    lowered = value.casefold()
    currency = None
    if "usd" in lowered or "$" in value:
        currency = "USD"
    elif "php" in lowered or "₱" in value:
        currency = "PHP"
    period = "year" if any(token in lowered for token in ("per year", "/year", "annual", "yr")) else None
    matches = [int(match.replace(",", "")) for match in re.findall(r"\b\d{2,3}(?:,\d{3})+\b", value)]
    if len(matches) >= 2:
        return min(matches), max(matches), currency, period
    if len(matches) == 1:
        return matches[0], matches[0], currency, period
    return None, None, currency, period


def description_hash(value: str) -> str:
    return sha256_hexdigest(value)
