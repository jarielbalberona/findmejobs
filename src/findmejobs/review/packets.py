from __future__ import annotations
import re
from html import unescape

from findmejobs.domain.job import CanonicalJob
from findmejobs.domain.review import HTML_TAG_RE, ReviewPacketModel
from findmejobs.utils.text import truncate_text

MAX_PACKET_BYTES = 16 * 1024
SUSPICIOUS_REVIEW_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"developer\s+instructions", re.IGNORECASE),
    re.compile(r"tool\s+call", re.IGNORECASE),
)


def build_review_packet(packet_id: str, cluster_id: str, job: CanonicalJob, total_score: float, score_breakdown: dict[str, float]) -> ReviewPacketModel:
    sanitized_description = sanitize_review_text(job.description_text)
    packet = ReviewPacketModel(
        packet_id=packet_id,
        packet_version="v1",
        cluster_id=cluster_id,
        company_name=job.company_name,
        title=job.title,
        location=job.location_text,
        employment_type=job.employment_type,
        seniority=job.seniority,
        salary_summary=_salary_summary(job),
        posted_at=job.posted_at,
        canonical_url=job.canonical_url,
        score_total=round(total_score, 2),
        score_breakdown=score_breakdown,
        matched_signals=[name for name, value in score_breakdown.items() if value > 0],
        description_excerpt=truncate_text(sanitized_description, 2000),
    )
    return enforce_packet_limit(packet)


def enforce_packet_limit(packet: ReviewPacketModel) -> ReviewPacketModel:
    while len(packet.model_dump_json().encode("utf-8")) > MAX_PACKET_BYTES and len(packet.description_excerpt) > 200:
        packet.description_excerpt = truncate_text(packet.description_excerpt, max(200, len(packet.description_excerpt) - 250))
    if len(packet.model_dump_json().encode("utf-8")) > MAX_PACKET_BYTES:
        raise ValueError("packet exceeds maximum size after sanitization")
    return packet


def _salary_summary(job: CanonicalJob) -> str | None:
    if job.salary_min is None and job.salary_max is None:
        return None
    parts = []
    if job.salary_min is not None:
        parts.append(str(job.salary_min))
    if job.salary_max is not None and job.salary_max != job.salary_min:
        parts.append(str(job.salary_max))
    currency = job.salary_currency or "USD"
    period = job.salary_period or "year"
    return f"{currency} {'-'.join(parts)} / {period}"


def sanitize_review_text(value: str) -> str:
    normalized = value or ""
    normalized = unescape(normalized)
    normalized = HTML_TAG_RE.sub("", normalized)
    cleaned_lines: list[str] = []
    for line in normalized.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in SUSPICIOUS_REVIEW_PATTERNS):
            continue
        cleaned_lines.append(stripped)
    return " ".join(cleaned_lines)
