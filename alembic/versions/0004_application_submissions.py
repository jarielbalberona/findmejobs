"""application submissions tracking

Revision ID: 0004_application_submissions
Revises: 0003_ph_board_observability
Create Date: 2026-03-22 22:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_application_submissions"
down_revision = "0003_ph_board_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_submissions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("job_id", sa.String(length=26), nullable=False),
        sa.Column("cluster_id", sa.String(length=26), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["job_clusters.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["normalized_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_submissions_job_id", "application_submissions", ["job_id"])
    op.create_index("ix_application_submissions_cluster_id", "application_submissions", ["cluster_id"])
    op.create_index("ix_application_submissions_status", "application_submissions", ["status"])
    op.create_index("ix_application_submissions_submitted_at", "application_submissions", ["submitted_at"])
    op.create_index("ix_application_submissions_created_at", "application_submissions", ["created_at"])
    op.create_index("ix_application_submissions_updated_at", "application_submissions", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_application_submissions_updated_at", table_name="application_submissions")
    op.drop_index("ix_application_submissions_created_at", table_name="application_submissions")
    op.drop_index("ix_application_submissions_submitted_at", table_name="application_submissions")
    op.drop_index("ix_application_submissions_status", table_name="application_submissions")
    op.drop_index("ix_application_submissions_cluster_id", table_name="application_submissions")
    op.drop_index("ix_application_submissions_job_id", table_name="application_submissions")
    op.drop_table("application_submissions")
