from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from findmejobs.config.models import (
    AppConfig,
    AshbySourceConfig,
    BossjobPHSourceConfig,
    BreezyHRSourceConfig,
    DirectPageSourceConfig,
    FounditPHSourceConfig,
    GreenhouseSourceConfig,
    JobStreetPHSourceConfig,
    JobviteSourceConfig,
    KalibrrSourceConfig,
    LeverSourceConfig,
    RSSSourceConfig,
    SmartRecruitersSourceConfig,
    SourceConfig,
    WorkableSourceConfig,
)
from findmejobs.db.repositories import (
    create_fetch_run,
    finish_fetch_run,
    get_or_create_raw_document,
    upsert_normalized_job,
    upsert_source,
    upsert_source_job,
)
from findmejobs.dedupe.clustering import assign_job_cluster
from findmejobs.ingestion.adapters.ashby import AshbyAdapter
from findmejobs.ingestion.adapters.bossjob_ph import BossjobPHAdapter
from findmejobs.ingestion.adapters.breezy_hr import BreezyHRAdapter
from findmejobs.ingestion.adapters.direct_page import DirectPageAdapter
from findmejobs.ingestion.adapters.foundit_ph import FounditPHAdapter
from findmejobs.ingestion.adapters.greenhouse import GreenhouseAdapter
from findmejobs.ingestion.adapters.jobstreet_ph import JobStreetPHAdapter
from findmejobs.ingestion.adapters.jobvite import JobviteAdapter
from findmejobs.ingestion.adapters.kalibrr import KalibrrAdapter
from findmejobs.ingestion.adapters.lever import LeverAdapter
from findmejobs.ingestion.adapters.rss import RSSAdapter
from findmejobs.ingestion.adapters.smartrecruiters import SmartRecruitersAdapter
from findmejobs.ingestion.adapters.workable import WorkableAdapter
from findmejobs.ingestion.fetch import fetch_to_artifact
from findmejobs.normalization.canonicalize import normalize_job
from findmejobs.utils.time import utcnow

LOGGER = logging.getLogger(__name__)


def run_ingest(
    session: Session,
    app_config: AppConfig,
    source_configs: list[SourceConfig],
    id_factory,
    *,
    client_factory=None,
    fetcher=None,
    adapter_builder=None,
) -> dict[str, int]:
    counts = {
        "sources": 0,
        "records": 0,
        "normalized": 0,
        "successful_sources": 0,
        "failed_sources": 0,
        "inserted": 0,
        "updated": 0,
        "dedupe_merges": 0,
    }
    client_factory = client_factory or httpx.Client
    fetcher = fetcher or fetch_to_artifact
    adapter_builder = adapter_builder or build_adapter
    with client_factory(
        timeout=app_config.http.timeout_seconds,
        headers={"User-Agent": app_config.http.user_agent},
    ) as client:
        for source_config in source_configs:
            if not source_config.enabled:
                continue
            counts["sources"] += 1
            source = upsert_source(session, source_config, id_factory)
            fetch_run = create_fetch_run(session, source.id, id_factory)
            session.commit()
            adapter = adapter_builder(source_config)
            try:
                artifact = _fetch_artifact(
                    fetcher,
                    client,
                    adapter.build_url(source_config),
                    app_config,
                    app_config.storage.raw_dir,
                    source.name,
                    headers=adapter.build_headers(source_config),
                )
                raw_document = get_or_create_raw_document(session, source.id, fetch_run.id, artifact, id_factory)
                session.commit()
                records, parse_stats = adapter.parse_with_stats(artifact, source_config)
                parsed_count = len(records)
                if source_config.fetch_cap is not None:
                    records = records[: source_config.fetch_cap]
                processed_count = len(records)
                fetch_run.item_count = processed_count
                source_inserted = 0
                source_updated = 0
                source_normalized = 0
                source_merges = 0
                source_failed = 0
                for record in records:
                    if _blocked_by_source_config(record, source_config):
                        continue
                    source_job, created_source_job = upsert_source_job(session, source.id, raw_document.id, fetch_run.id, record, id_factory)
                    canonical = normalize_job(
                        source_job.id,
                        source.id,
                        source_job.seen_at,
                        record,
                        source_name=source.name,
                        source_kind=source.kind,
                        source_priority=source.priority,
                        source_trust_weight=source.trust_weight,
                    )
                    normalized, created_normalized = upsert_normalized_job(session, canonical, id_factory)
                    _cluster, merged = assign_job_cluster(session, normalized, id_factory)
                    counts["records"] += 1
                    if created_source_job or created_normalized:
                        counts["inserted"] += 1
                        source_inserted += 1
                    else:
                        counts["updated"] += 1
                        source_updated += 1
                    if merged:
                        counts["dedupe_merges"] += 1
                        source_merges += 1
                    if normalized.normalization_status == "valid":
                        counts["normalized"] += 1
                        source_normalized += 1
                    else:
                        source_failed += 1
                finish_fetch_run(
                    fetch_run,
                    status="success",
                    http_status=artifact.status_code,
                    item_count=processed_count,
                    raw_seen_count=parse_stats.raw_seen_count,
                    seen_count=parsed_count,
                    skipped_count=parse_stats.skipped_count,
                    inserted_count=source_inserted,
                    updated_count=source_updated,
                    failed_count=source_failed,
                    dedupe_merge_count=source_merges,
                    normalized_valid_count=source_normalized,
                )
                source.last_successful_run_at = utcnow()
                counts["successful_sources"] += 1
                session.commit()
            except Exception as exc:
                session.rollback()
                fetch_run = session.get(type(fetch_run), fetch_run.id)
                if fetch_run is None:
                    raise
                finish_fetch_run(
                    fetch_run,
                    status="failed",
                    http_status=None,
                    item_count=0,
                    raw_seen_count=0,
                    seen_count=0,
                    skipped_count=0,
                    failed_count=1,
                    parse_error_count=1,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
                source = session.get(type(source), source.id)
                if source is not None:
                    source.last_failed_run_at = utcnow()
                session.add(fetch_run)
                session.commit()
                counts["failed_sources"] += 1
                LOGGER.exception("ingest source failed", extra={"payload": {"source": source.name}})
    return counts


def build_adapter(config: SourceConfig):
    if isinstance(config, RSSSourceConfig):
        return RSSAdapter()
    if isinstance(config, GreenhouseSourceConfig):
        return GreenhouseAdapter()
    if isinstance(config, LeverSourceConfig):
        return LeverAdapter()
    if isinstance(config, SmartRecruitersSourceConfig):
        return SmartRecruitersAdapter()
    if isinstance(config, WorkableSourceConfig):
        return WorkableAdapter()
    if isinstance(config, BreezyHRSourceConfig):
        return BreezyHRAdapter()
    if isinstance(config, JobviteSourceConfig):
        return JobviteAdapter()
    if isinstance(config, AshbySourceConfig):
        return AshbyAdapter()
    if isinstance(config, JobStreetPHSourceConfig):
        return JobStreetPHAdapter()
    if isinstance(config, KalibrrSourceConfig):
        return KalibrrAdapter()
    if isinstance(config, BossjobPHSourceConfig):
        return BossjobPHAdapter()
    if isinstance(config, FounditPHSourceConfig):
        return FounditPHAdapter()
    if isinstance(config, DirectPageSourceConfig):
        return DirectPageAdapter()
    raise ValueError(f"unsupported source kind {config.kind}")


def _blocked_by_source_config(record, source_config: SourceConfig) -> bool:
    title_lower = record.title.casefold()
    return any(keyword.casefold() in title_lower for keyword in source_config.blocked_title_keywords)


def _fetch_artifact(fetcher, client, url: str, app_config: AppConfig, raw_dir, source_name: str, *, headers: dict[str, str]):
    try:
        return fetcher(client, url, app_config, raw_dir, source_name, headers=headers)
    except TypeError as exc:
        if "headers" not in str(exc):
            raise
        return fetcher(client, url, app_config, raw_dir, source_name)
