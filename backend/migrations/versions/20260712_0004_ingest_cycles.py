"""Persist complete ingest-cycle outcomes for operational health.

Revision ID: 20260712_0004
Revises: 20260712_0003
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_0004"
down_revision: str | None = "20260712_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_cycles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("targets", sa.Integer(), nullable=False),
        sa.Column("saved", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "targets >= 0", name=op.f("ck_ingest_cycles_targets_nonnegative")
        ),
        sa.CheckConstraint(
            "saved >= 0", name=op.f("ck_ingest_cycles_saved_nonnegative")
        ),
        sa.CheckConstraint(
            "failed >= 0", name=op.f("ck_ingest_cycles_failed_nonnegative")
        ),
        sa.CheckConstraint(
            "saved + failed <= targets",
            name=op.f("ck_ingest_cycles_result_within_targets"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'complete', 'partial', 'failed')",
            name=op.f("ck_ingest_cycles_status_values"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status != 'running' AND completed_at IS NOT NULL)",
            name=op.f("ck_ingest_cycles_completion_matches_status"),
        ),
        sa.CheckConstraint(
            "status != 'complete' OR "
            "(targets > 0 AND saved = targets AND failed = 0)",
            name=op.f("ck_ingest_cycles_complete_result"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_cycles")),
    )
    op.create_index(
        "ix_ingest_cycles_started_at",
        "ingest_cycles",
        [sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_cycles_started_at", table_name="ingest_cycles")
    op.drop_table("ingest_cycles")
