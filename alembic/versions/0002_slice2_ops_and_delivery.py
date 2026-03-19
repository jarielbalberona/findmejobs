"""slice2 ops and delivery

Revision ID: 0002_slice2
Revises: 0001_slice1
Create Date: 2026-03-19 18:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_slice2"
down_revision = "0001_slice1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sources", sa.Column("trust_weight", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("sources", sa.Column("fetch_cap", sa.Integer(), nullable=True))
    op.add_column("sources", sa.Column("last_successful_run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sources", sa.Column("last_failed_run_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("source_fetch_runs", sa.Column("seen_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("source_fetch_runs", sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("source_fetch_runs", sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("source_fetch_runs", sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("source_fetch_runs", sa.Column("parse_error_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("source_fetch_runs", sa.Column("dedupe_merge_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("source_fetch_runs", sa.Column("normalized_valid_count", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "job_feedback",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("cluster_id", sa.String(length=26)),
        sa.Column("feedback_type", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=255)),
        sa.Column("title_keyword", sa.String(length=255)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["job_clusters.id"]),
    )
    op.create_index("ix_job_feedback_cluster_id", "job_feedback", ["cluster_id"])
    op.create_index("ix_job_feedback_feedback_type", "job_feedback", ["feedback_type"])
    op.create_index("ix_job_feedback_company_name", "job_feedback", ["company_name"])
    op.create_index("ix_job_feedback_title_keyword", "job_feedback", ["title_keyword"])
    op.create_index("ix_job_feedback_created_at", "job_feedback", ["created_at"])

    op.create_table(
        "digests",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("digest_date", sa.String(length=32), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("resend_of_digest_id", sa.String(length=26)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["resend_of_digest_id"], ["digests.id"]),
    )
    op.create_index("ix_digests_channel", "digests", ["channel"])
    op.create_index("ix_digests_digest_date", "digests", ["digest_date"])
    op.create_index("ix_digests_status", "digests", ["status"])
    op.create_index("ix_digests_sent_at", "digests", ["sent_at"])

    op.create_table(
        "digest_items",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("digest_id", sa.String(length=26), nullable=False),
        sa.Column("cluster_id", sa.String(length=26), nullable=False),
        sa.Column("review_id", sa.String(length=26), nullable=False),
        sa.Column("job_score_id", sa.String(length=26), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("item_json", sa.JSON(), nullable=False),
        sa.Column("score_at_send", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["digest_id"], ["digests.id"]),
        sa.ForeignKeyConstraint(["cluster_id"], ["job_clusters.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["openclaw_reviews.id"]),
        sa.ForeignKeyConstraint(["job_score_id"], ["job_scores.id"]),
        sa.UniqueConstraint("digest_id", "cluster_id", name="uq_digest_cluster"),
    )
    op.create_index("ix_digest_items_digest_id", "digest_items", ["digest_id"])
    op.create_index("ix_digest_items_cluster_id", "digest_items", ["cluster_id"])
    op.create_index("ix_digest_items_review_id", "digest_items", ["review_id"])
    op.create_index("ix_digest_items_job_score_id", "digest_items", ["job_score_id"])

    op.create_table(
        "delivery_events",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("digest_id", sa.String(length=26)),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255)),
        sa.Column("error_message", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["digest_id"], ["digests.id"]),
    )
    op.create_index("ix_delivery_events_digest_id", "delivery_events", ["digest_id"])
    op.create_index("ix_delivery_events_channel", "delivery_events", ["channel"])
    op.create_index("ix_delivery_events_status", "delivery_events", ["status"])
    op.create_index("ix_delivery_events_created_at", "delivery_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_delivery_events_created_at", table_name="delivery_events")
    op.drop_index("ix_delivery_events_status", table_name="delivery_events")
    op.drop_index("ix_delivery_events_channel", table_name="delivery_events")
    op.drop_index("ix_delivery_events_digest_id", table_name="delivery_events")
    op.drop_table("delivery_events")

    op.drop_index("ix_digest_items_job_score_id", table_name="digest_items")
    op.drop_index("ix_digest_items_review_id", table_name="digest_items")
    op.drop_index("ix_digest_items_cluster_id", table_name="digest_items")
    op.drop_index("ix_digest_items_digest_id", table_name="digest_items")
    op.drop_table("digest_items")

    op.drop_index("ix_digests_sent_at", table_name="digests")
    op.drop_index("ix_digests_status", table_name="digests")
    op.drop_index("ix_digests_digest_date", table_name="digests")
    op.drop_index("ix_digests_channel", table_name="digests")
    op.drop_table("digests")

    op.drop_index("ix_job_feedback_created_at", table_name="job_feedback")
    op.drop_index("ix_job_feedback_title_keyword", table_name="job_feedback")
    op.drop_index("ix_job_feedback_company_name", table_name="job_feedback")
    op.drop_index("ix_job_feedback_feedback_type", table_name="job_feedback")
    op.drop_index("ix_job_feedback_cluster_id", table_name="job_feedback")
    op.drop_table("job_feedback")

    op.drop_column("source_fetch_runs", "normalized_valid_count")
    op.drop_column("source_fetch_runs", "dedupe_merge_count")
    op.drop_column("source_fetch_runs", "parse_error_count")
    op.drop_column("source_fetch_runs", "failed_count")
    op.drop_column("source_fetch_runs", "updated_count")
    op.drop_column("source_fetch_runs", "inserted_count")
    op.drop_column("source_fetch_runs", "seen_count")

    op.drop_column("sources", "last_failed_run_at")
    op.drop_column("sources", "last_successful_run_at")
    op.drop_column("sources", "fetch_cap")
    op.drop_column("sources", "trust_weight")
    op.drop_column("sources", "priority")
