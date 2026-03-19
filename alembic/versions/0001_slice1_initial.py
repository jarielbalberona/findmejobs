"""slice1 initial schema

Revision ID: 0001_slice1
Revises:
Create Date: 2026-03-19 10:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_slice1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "source_fetch_runs",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("source_id", sa.String(length=26), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
    )
    op.create_index("ix_source_fetch_runs_source_id", "source_fetch_runs", ["source_id"])
    op.create_index("ix_source_fetch_runs_started_at", "source_fetch_runs", ["started_at"])
    op.create_index("ix_source_fetch_runs_status", "source_fetch_runs", ["status"])

    op.create_table(
        "raw_documents",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("source_id", sa.String(length=26), nullable=False),
        sa.Column("fetch_run_id", sa.String(length=26), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("content_type", sa.String(length=255)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fetch_run_id"], ["source_fetch_runs.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.UniqueConstraint("source_id", "sha256", name="uq_raw_document_source_sha"),
    )
    op.create_index("ix_raw_documents_source_id", "raw_documents", ["source_id"])
    op.create_index("ix_raw_documents_fetch_run_id", "raw_documents", ["fetch_run_id"])
    op.create_index("ix_raw_documents_canonical_url", "raw_documents", ["canonical_url"])
    op.create_index("ix_raw_documents_fetched_at", "raw_documents", ["fetched_at"])

    op.create_table(
        "source_jobs",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("source_id", sa.String(length=26), nullable=False),
        sa.Column("raw_document_id", sa.String(length=26), nullable=False),
        sa.Column("fetch_run_id", sa.String(length=26), nullable=False),
        sa.Column("source_job_key", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("apply_url", sa.Text()),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["raw_document_id"], ["raw_documents.id"]),
        sa.ForeignKeyConstraint(["fetch_run_id"], ["source_fetch_runs.id"]),
        sa.UniqueConstraint("source_id", "source_job_key", name="uq_source_jobs_source_key"),
    )
    op.create_index("ix_source_jobs_source_id", "source_jobs", ["source_id"])
    op.create_index("ix_source_jobs_raw_document_id", "source_jobs", ["raw_document_id"])
    op.create_index("ix_source_jobs_fetch_run_id", "source_jobs", ["fetch_run_id"])
    op.create_index("ix_source_jobs_seen_at", "source_jobs", ["seen_at"])

    op.create_table(
        "normalized_jobs",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("source_job_id", sa.String(length=26), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("location_text", sa.String(length=255), nullable=False),
        sa.Column("location_type", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=2)),
        sa.Column("city", sa.String(length=255)),
        sa.Column("region", sa.String(length=255)),
        sa.Column("seniority", sa.String(length=32)),
        sa.Column("employment_type", sa.String(length=32)),
        sa.Column("salary_min", sa.Integer()),
        sa.Column("salary_max", sa.Integer()),
        sa.Column("salary_currency", sa.String(length=8)),
        sa.Column("salary_period", sa.String(length=32)),
        sa.Column("description_text", sa.Text(), nullable=False),
        sa.Column("description_sha256", sa.String(length=64), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalization_status", sa.String(length=32), nullable=False),
        sa.Column("normalization_errors_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["source_job_id"], ["source_jobs.id"]),
        sa.UniqueConstraint("source_job_id"),
    )
    op.create_index("ix_normalized_jobs_canonical_url", "normalized_jobs", ["canonical_url"])
    op.create_index("ix_normalized_jobs_company_name", "normalized_jobs", ["company_name"])
    op.create_index("ix_normalized_jobs_title", "normalized_jobs", ["title"])
    op.create_index("ix_normalized_jobs_country_code", "normalized_jobs", ["country_code"])
    op.create_index("ix_normalized_jobs_posted_at", "normalized_jobs", ["posted_at"])
    op.create_index("ix_normalized_jobs_normalization_status", "normalized_jobs", ["normalization_status"])

    op.create_table(
        "job_clusters",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("cluster_key", sa.String(length=255), nullable=False),
        sa.Column("representative_job_id", sa.String(length=26)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["representative_job_id"], ["normalized_jobs.id"]),
        sa.UniqueConstraint("cluster_key"),
    )
    op.create_index("ix_job_clusters_representative_job_id", "job_clusters", ["representative_job_id"])
    op.create_index("ix_job_clusters_updated_at", "job_clusters", ["updated_at"])

    op.create_table(
        "job_cluster_members",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("cluster_id", sa.String(length=26), nullable=False),
        sa.Column("normalized_job_id", sa.String(length=26), nullable=False),
        sa.Column("match_rule", sa.String(length=64), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("is_representative", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["job_clusters.id"]),
        sa.ForeignKeyConstraint(["normalized_job_id"], ["normalized_jobs.id"]),
        sa.UniqueConstraint("cluster_id", "normalized_job_id", name="uq_cluster_member"),
    )
    op.create_index("ix_job_cluster_members_cluster_id", "job_cluster_members", ["cluster_id"])
    op.create_index("ix_job_cluster_members_normalized_job_id", "job_cluster_members", ["normalized_job_id"])

    op.create_table(
        "profiles",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("version"),
    )
    op.create_index("ix_profiles_is_active", "profiles", ["is_active"])

    op.create_table(
        "rank_models",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("version"),
    )
    op.create_index("ix_rank_models_is_active", "rank_models", ["is_active"])

    op.create_table(
        "job_scores",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("cluster_id", sa.String(length=26), nullable=False),
        sa.Column("profile_id", sa.String(length=26), nullable=False),
        sa.Column("rank_model_id", sa.String(length=26), nullable=False),
        sa.Column("passed_hard_filters", sa.Boolean(), nullable=False),
        sa.Column("hard_filter_reasons_json", sa.JSON(), nullable=False),
        sa.Column("score_total", sa.Float(), nullable=False),
        sa.Column("score_breakdown_json", sa.JSON(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["job_clusters.id"]),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.ForeignKeyConstraint(["rank_model_id"], ["rank_models.id"]),
        sa.UniqueConstraint("cluster_id", "profile_id", "rank_model_id", name="uq_job_score_cluster_profile_model"),
    )
    op.create_index("ix_job_scores_cluster_id", "job_scores", ["cluster_id"])
    op.create_index("ix_job_scores_profile_id", "job_scores", ["profile_id"])
    op.create_index("ix_job_scores_rank_model_id", "job_scores", ["rank_model_id"])
    op.create_index("ix_job_scores_passed_hard_filters", "job_scores", ["passed_hard_filters"])
    op.create_index("ix_job_scores_score_total", "job_scores", ["score_total"])
    op.create_index("ix_job_scores_scored_at", "job_scores", ["scored_at"])

    op.create_table(
        "review_packets",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("cluster_id", sa.String(length=26), nullable=False),
        sa.Column("job_score_id", sa.String(length=26), nullable=False),
        sa.Column("packet_version", sa.String(length=32), nullable=False),
        sa.Column("packet_json", sa.JSON(), nullable=False),
        sa.Column("packet_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["cluster_id"], ["job_clusters.id"]),
        sa.ForeignKeyConstraint(["job_score_id"], ["job_scores.id"]),
        sa.UniqueConstraint("cluster_id", "job_score_id", "packet_version", name="uq_review_packet_cluster_score_version"),
    )
    op.create_index("ix_review_packets_cluster_id", "review_packets", ["cluster_id"])
    op.create_index("ix_review_packets_job_score_id", "review_packets", ["job_score_id"])
    op.create_index("ix_review_packets_status", "review_packets", ["status"])
    op.create_index("ix_review_packets_built_at", "review_packets", ["built_at"])

    op.create_table(
        "openclaw_reviews",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("review_packet_id", sa.String(length=26), nullable=False),
        sa.Column("provider_review_id", sa.String(length=255)),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confidence_label", sa.String(length=32)),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("draft_summary", sa.Text()),
        sa.Column("draft_actions_json", sa.JSON(), nullable=False),
        sa.Column("raw_response_json", sa.JSON(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_packet_id"], ["review_packets.id"]),
        sa.UniqueConstraint("review_packet_id"),
    )
    op.create_index("ix_openclaw_reviews_review_packet_id", "openclaw_reviews", ["review_packet_id"])
    op.create_index("ix_openclaw_reviews_decision", "openclaw_reviews", ["decision"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("command", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stats_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_pipeline_runs_command", "pipeline_runs", ["command"])
    op.create_index("ix_pipeline_runs_started_at", "pipeline_runs", ["started_at"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_status", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_started_at", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_command", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")

    op.drop_index("ix_openclaw_reviews_decision", table_name="openclaw_reviews")
    op.drop_index("ix_openclaw_reviews_review_packet_id", table_name="openclaw_reviews")
    op.drop_table("openclaw_reviews")

    op.drop_index("ix_review_packets_built_at", table_name="review_packets")
    op.drop_index("ix_review_packets_status", table_name="review_packets")
    op.drop_index("ix_review_packets_job_score_id", table_name="review_packets")
    op.drop_index("ix_review_packets_cluster_id", table_name="review_packets")
    op.drop_table("review_packets")

    op.drop_index("ix_job_scores_scored_at", table_name="job_scores")
    op.drop_index("ix_job_scores_score_total", table_name="job_scores")
    op.drop_index("ix_job_scores_passed_hard_filters", table_name="job_scores")
    op.drop_index("ix_job_scores_rank_model_id", table_name="job_scores")
    op.drop_index("ix_job_scores_profile_id", table_name="job_scores")
    op.drop_index("ix_job_scores_cluster_id", table_name="job_scores")
    op.drop_table("job_scores")

    op.drop_index("ix_rank_models_is_active", table_name="rank_models")
    op.drop_table("rank_models")

    op.drop_index("ix_profiles_is_active", table_name="profiles")
    op.drop_table("profiles")

    op.drop_index("ix_job_cluster_members_normalized_job_id", table_name="job_cluster_members")
    op.drop_index("ix_job_cluster_members_cluster_id", table_name="job_cluster_members")
    op.drop_table("job_cluster_members")

    op.drop_index("ix_job_clusters_updated_at", table_name="job_clusters")
    op.drop_index("ix_job_clusters_representative_job_id", table_name="job_clusters")
    op.drop_table("job_clusters")

    op.drop_index("ix_normalized_jobs_normalization_status", table_name="normalized_jobs")
    op.drop_index("ix_normalized_jobs_posted_at", table_name="normalized_jobs")
    op.drop_index("ix_normalized_jobs_country_code", table_name="normalized_jobs")
    op.drop_index("ix_normalized_jobs_title", table_name="normalized_jobs")
    op.drop_index("ix_normalized_jobs_company_name", table_name="normalized_jobs")
    op.drop_index("ix_normalized_jobs_canonical_url", table_name="normalized_jobs")
    op.drop_table("normalized_jobs")

    op.drop_index("ix_source_jobs_seen_at", table_name="source_jobs")
    op.drop_index("ix_source_jobs_fetch_run_id", table_name="source_jobs")
    op.drop_index("ix_source_jobs_raw_document_id", table_name="source_jobs")
    op.drop_index("ix_source_jobs_source_id", table_name="source_jobs")
    op.drop_table("source_jobs")

    op.drop_index("ix_raw_documents_fetched_at", table_name="raw_documents")
    op.drop_index("ix_raw_documents_canonical_url", table_name="raw_documents")
    op.drop_index("ix_raw_documents_fetch_run_id", table_name="raw_documents")
    op.drop_index("ix_raw_documents_source_id", table_name="raw_documents")
    op.drop_table("raw_documents")

    op.drop_index("ix_source_fetch_runs_status", table_name="source_fetch_runs")
    op.drop_index("ix_source_fetch_runs_started_at", table_name="source_fetch_runs")
    op.drop_index("ix_source_fetch_runs_source_id", table_name="source_fetch_runs")
    op.drop_table("source_fetch_runs")

    op.drop_table("sources")
