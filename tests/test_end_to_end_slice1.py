from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from findmejobs.config.loader import load_app_config, load_profile_config, load_source_configs
from findmejobs.db.models import JobCluster, JobScore, NormalizedJob, ReviewPacket, SourceFetchRun
from findmejobs.db.repositories import upsert_job_score, upsert_profile, upsert_rank_model
from findmejobs.db.session import create_session_factory
from findmejobs.domain.source import FetchArtifact
from findmejobs.ingestion.orchestrator import run_ingest
from findmejobs.ranking.engine import rank_job
from findmejobs.review.service import canonical_job_from_row, export_review_packets
from findmejobs.utils.ids import new_id
from findmejobs.utils.time import utcnow


def test_end_to_end_slice1_flow_with_visible_failure(
    fixtures_dir: Path,
    migrated_runtime_config_files: tuple[Path, Path, Path],
    monkeypatch,
) -> None:
    app_path, profile_path, sources_dir = migrated_runtime_config_files
    app_config = load_app_config(app_path)
    profile = load_profile_config(profile_path)
    sources = load_source_configs(sources_dir)
    session_factory = create_session_factory(app_config.database.url)

    fixture_map = {
        "rss-source": (fixtures_dir / "rss_feed.xml").read_bytes(),
        "acme": (fixtures_dir / "greenhouse_bad.json").read_bytes(),
    }
    content_types = {
        "rss-source": "application/rss+xml",
        "acme": "application/json",
    }

    def fake_fetcher(client, url, app_config, raw_root, source_name):
        body = fixture_map[source_name]
        suffix = ".xml" if content_types[source_name].endswith("xml") else ".json"
        target = raw_root / source_name / f"payload{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return FetchArtifact(
            fetched_url=url,
            final_url=url,
            status_code=200,
            content_type=content_types[source_name],
            headers={},
            fetched_at=utcnow(),
            body_bytes=body,
            sha256=f"sha-{source_name}",
            storage_path=str(target),
        )

    with session_factory() as session:
        counts = run_ingest(session, app_config, sources, new_id, fetcher=fake_fetcher)
        assert counts["sources"] == 2
        assert counts["failed_sources"] == 1
        assert session.scalar(select(func.count()).select_from(JobCluster)) == 2
        statuses = set(session.scalars(select(SourceFetchRun.status)))
        assert {"success", "failed"} <= statuses

        profile_row = upsert_profile(session, profile, new_id)
        rank_model = upsert_rank_model(session, profile, new_id)
        clusters = session.execute(
            select(JobCluster.id, JobCluster.representative_job_id)
        ).all()
        totals: dict[str, float] = {}
        for cluster_id, representative_job_id in clusters:
            job_row = session.get(NormalizedJob, representative_job_id)
            if job_row.normalization_status != "valid":
                continue
            canonical = canonical_job_from_row(job_row)
            breakdown = rank_job(canonical, profile)
            upsert_job_score(session, cluster_id, profile_row.id, rank_model.id, breakdown, new_id)
            totals[canonical.title] = breakdown.total
        session.commit()
        assert totals["Backend Engineer"] > totals["Platform Engineer"]

    captured = {}

    class RecordingClient:
        def __init__(self, outbox_dir, inbox_dir):
            self.outbox_dir = outbox_dir
            self.inbox_dir = inbox_dir

        def export_packet(self, packet):
            captured[packet.packet_id] = packet
            return self.outbox_dir / f"{packet.packet_id}.json"

        def load_results(self):
            return []

    monkeypatch.setattr("findmejobs.review.service.FilesystemOpenClawClient", RecordingClient)
    with session_factory() as session:
        exported = export_review_packets(session, app_config, profile, new_id)
        assert exported == 1
        exported = export_review_packets(session, app_config, profile, new_id)
        assert exported == 0
        assert session.scalar(select(func.count()).select_from(JobScore)) >= 1
        assert session.scalar(select(func.count()).select_from(ReviewPacket)) == 1
        assert captured
