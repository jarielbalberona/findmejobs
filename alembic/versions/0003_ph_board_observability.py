"""ph board observability counters

Revision ID: 0003_ph_board_observability
Revises: 0002_slice2
Create Date: 2026-03-19 23:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_ph_board_observability"
down_revision = "0002_slice2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_fetch_runs", sa.Column("raw_seen_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("source_fetch_runs", sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("source_fetch_runs", "skipped_count")
    op.drop_column("source_fetch_runs", "raw_seen_count")
