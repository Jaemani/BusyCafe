"""Record the deterministic scoring model version on materialized scores.

Revision ID: 20260712_0003
Revises: 20260711_0002
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_0003"
down_revision: str | None = "20260711_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_MODEL_VERSION = "v1-idw-point"


def upgrade() -> None:
    with op.batch_alter_table("cafe_scores") as batch:
        batch.add_column(sa.Column("model_version", sa.String(length=64), nullable=True))

    cafe_scores = sa.table(
        "cafe_scores",
        sa.column("model_version", sa.String(length=64)),
    )
    op.execute(
        cafe_scores.update()
        .where(cafe_scores.c.model_version.is_(None))
        .values(model_version=LEGACY_MODEL_VERSION)
    )

    with op.batch_alter_table("cafe_scores") as batch:
        batch.alter_column(
            "model_version",
            existing_type=sa.String(length=64),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("cafe_scores") as batch:
        batch.drop_column("model_version")
