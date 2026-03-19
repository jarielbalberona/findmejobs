"""Read-only job listing for operator preview (CLI / reports)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from findmejobs.config.models import ProfileConfig
from findmejobs.db.models import JobCluster, JobScore, NormalizedJob, Profile, RankModel, Source, SourceJob

JobListStatus = Literal["eligible", "below_threshold", "hard_filtered"]


@dataclass(frozen=True)
class JobPreview:
    job_id: str
    cluster_id: str
    title: str
    company_name: str
    location_text: str
    source_name: str
    score_total: float
    status: JobListStatus
    hard_filter_reasons: tuple[str, ...]
    tags: tuple[str, ...]
    matched_signals: tuple[str, ...]
    description_snippet: str
    canonical_url: str | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "cluster_id": self.cluster_id,
            "title": self.title,
            "company_name": self.company_name,
            "location_text": self.location_text,
            "source": self.source_name,
            "score": self.score_total,
            "status": self.status,
            "hard_filter_reasons": list(self.hard_filter_reasons),
            "tags": list(self.tags),
            "matched_signals": list(self.matched_signals),
            "description_snippet": self.description_snippet,
            "canonical_url": self.canonical_url,
        }


def _snippet(text: str, max_len: int) -> str:
    one_line = re.sub(r"\s+", " ", text.strip())
    if len(one_line) <= max_len:
        return one_line
    if max_len <= 1:
        return "…"
    return one_line[: max_len - 1] + "…"


def _matched_signals(breakdown: dict) -> tuple[str, ...]:
    out: list[str] = []
    for name, value in breakdown.items():
        if isinstance(value, (int, float)) and value > 0:
            out.append(str(name))
    return tuple(sorted(out))


def _classify(
    *,
    passed_hard_filters: bool,
    score_total: float,
    minimum_score: float,
) -> JobListStatus:
    if not passed_hard_filters:
        return "hard_filtered"
    if score_total < minimum_score:
        return "below_threshold"
    return "eligible"


def fetch_job_previews(
    session: Session,
    profile: ProfileConfig,
    *,
    all_scored: bool,
    limit: int,
    snippet_length: int,
) -> list[JobPreview]:
    """Return representative normalized jobs with scores for the active profile + rank model."""
    profile_row = session.scalar(select(Profile).where(Profile.version == profile.version))
    rank_model = session.scalar(select(RankModel).where(RankModel.version == profile.rank_model_version))
    if profile_row is None or rank_model is None:
        return []

    stmt = (
        select(JobCluster, JobScore, NormalizedJob, Source)
        .join(JobScore, JobScore.cluster_id == JobCluster.id)
        .join(NormalizedJob, NormalizedJob.id == JobCluster.representative_job_id)
        .join(SourceJob, SourceJob.id == NormalizedJob.source_job_id)
        .join(Source, Source.id == SourceJob.source_id)
        .where(JobScore.profile_id == profile_row.id)
        .where(JobScore.rank_model_id == rank_model.id)
    )
    if not all_scored:
        stmt = (
            stmt.where(JobScore.passed_hard_filters.is_(True))
            .where(JobScore.score_total >= profile.ranking.minimum_score)
            .order_by(desc(JobScore.score_total), desc(JobScore.scored_at), JobCluster.id.asc())
        )
    else:
        stmt = stmt.order_by(
            desc(JobScore.passed_hard_filters),
            desc(JobScore.score_total),
            desc(JobScore.scored_at),
            JobCluster.id.asc(),
        )

    stmt = stmt.limit(limit)
    previews: list[JobPreview] = []
    for cluster, score, job, source in session.execute(stmt):
        reasons = tuple(score.hard_filter_reasons_json or [])
        status = _classify(
            passed_hard_filters=score.passed_hard_filters,
            score_total=score.score_total,
            minimum_score=profile.ranking.minimum_score,
        )
        breakdown = score.score_breakdown_json or {}
        tags = tuple(str(t) for t in (job.tags_json or []) if t is not None)
        previews.append(
            JobPreview(
                job_id=job.id,
                cluster_id=cluster.id,
                title=job.title,
                company_name=job.company_name,
                location_text=job.location_text,
                source_name=source.name,
                score_total=round(float(score.score_total), 2),
                status=status,
                hard_filter_reasons=reasons,
                tags=tags,
                matched_signals=_matched_signals(breakdown),
                description_snippet=_snippet(job.description_text, snippet_length),
                canonical_url=job.canonical_url,
            )
        )
    return previews


def format_job_previews_text(previews: list[JobPreview]) -> str:
    """Human-readable blocks for terminal output."""
    if not previews:
        return "No jobs matched. Try `findmejobs rank` first, or pass `--all-scored` / adjust filters.\n"
    lines: list[str] = []
    for p in previews:
        reasons = ", ".join(p.hard_filter_reasons) if p.hard_filter_reasons else "—"
        tags = ", ".join(p.tags) if p.tags else "—"
        signals = ", ".join(p.matched_signals) if p.matched_signals else "—"
        url = p.canonical_url or "—"
        lines.append(
            "\n".join(
                [
                    f"--- {p.title} @ {p.company_name} ---",
                    f"job_id={p.job_id}  cluster_id={p.cluster_id}  status={p.status}  score={p.score_total}",
                    f"source={p.source_name}  location={p.location_text or '—'}",
                    f"tags: {tags}",
                    f"matched_signals: {signals}",
                    f"hard_filters: {reasons}",
                    f"url: {url}",
                    f"snippet: {p.description_snippet}",
                    "",
                ]
            )
        )
    return "\n".join(lines).rstrip() + "\n"
