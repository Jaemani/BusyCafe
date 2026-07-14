"""Index snapshot fetch time used by the public health read model.

Revision ID: 20260714_0007
Revises: 20260714_0006
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0007"
down_revision: str | None = "20260714_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_snap_fetched_at",
        "hotspot_snapshots",
        [sa.text("fetched_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_snap_fetched_at", table_name="hotspot_snapshots")
