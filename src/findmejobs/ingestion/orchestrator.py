from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from findmejobs.config.models import AppConfig, GreenhouseSourceConfig, RSSSourceConfig, SourceConfig
from findmejobs.db.repositories import (
    create_fetch_run,
    finish_fetch_run,
    get_or_create_raw_document,
    upsert_normalized_job,
    upsert_source,
    upsert_source_job,
)
from findmejobs.dedupe.clustering import assign_job_cluster
from findmejobs.ingestion.adapters.greenhouse import GreenhouseAdapter
from findmejobs.ingestion.adapters.rss import RSSAdapter
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
    client_factory=httpx.Client,
    fetcher=fetch_to_artifact,
    adapter_builder=None,
) -> dict[str, int]:
    counts = {"sources": 0, "records": 0, "normalized": 0}
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
                artifact = fetcher(
                    client,
                    adapter.build_url(source_config),
                    app_config,
                    app_config.storage.raw_dir,
                    source.name,
                )
                raw_document = get_or_create_raw_document(session, source.id, fetch_run.id, artifact, id_factory)
                session.commit()
                records = adapter.parse(artifact, source_config)
                fetch_run.item_count = len(records)
                for record in records:
                    source_job = upsert_source_job(session, source.id, raw_document.id, fetch_run.id, record, id_factory)
                    canonical = normalize_job(source_job.id, source.id, source_job.seen_at, record)
                    normalized = upsert_normalized_job(session, canonical, id_factory)
                    assign_job_cluster(session, normalized, id_factory)
                    counts["records"] += 1
                    if normalized.normalization_status == "valid":
                        counts["normalized"] += 1
                finish_fetch_run(
                    fetch_run,
                    status="success",
                    http_status=artifact.status_code,
                    item_count=len(records),
                )
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
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
                session.add(fetch_run)
                session.commit()
                LOGGER.exception("ingest source failed", extra={"payload": {"source": source.name}})
    return counts


def build_adapter(config: SourceConfig):
    if isinstance(config, RSSSourceConfig):
        return RSSAdapter()
    if isinstance(config, GreenhouseSourceConfig):
        return GreenhouseAdapter()
    raise ValueError(f"unsupported source kind {config.kind}")
