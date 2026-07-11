"""Replace the active cafe identity with the Overture POI cache.

Revision ID: 20260711_0002
Revises: 20260711_0001
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260711_0002"
down_revision: str | None = "20260711_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # The legacy Kakao columns are retained only so a pre-release local DB can
    # migrate without destructive data loss. They are no longer mapped or read
    # by application code; preserved rows are disabled and require re-ingest.
    with op.batch_alter_table("cafes") as batch:
        batch.drop_index("ix_cafes_neighborhood_active")
        batch.alter_column("kakao_place_id", existing_type=sa.String(64), nullable=True)
        batch.add_column(sa.Column("overture_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("source_release", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("source_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("primary_category", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("website", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("source_json", JSON_VALUE, nullable=True))
        batch.add_column(sa.Column("external_links_json", JSON_VALUE, nullable=True))

    op.execute(
        "UPDATE cafes SET overture_id = 'legacy-kakao:' || id, "
        "source_release = 'legacy-kakao', source_confidence = 0.0, "
        "primary_category = 'legacy', active = false "
        "WHERE overture_id IS NULL"
    )

    with op.batch_alter_table("cafes") as batch:
        batch.alter_column("overture_id", existing_type=sa.String(64), nullable=False)
        batch.alter_column("source_release", existing_type=sa.String(32), nullable=False)
        batch.alter_column(
            "source_confidence", existing_type=sa.Float(), nullable=False
        )
        batch.alter_column(
            "primary_category", existing_type=sa.String(100), nullable=False
        )
        batch.create_unique_constraint("uq_cafes_overture_id", ["overture_id"])
        batch.create_check_constraint(
            "source_confidence_range",
            "source_confidence BETWEEN 0.0 AND 1.0",
        )
        batch.create_index("ix_cafes_active_bbox", ["active", "lng", "lat"])


def downgrade() -> None:
    with op.batch_alter_table("cafes") as batch:
        batch.drop_index("ix_cafes_active_bbox")
        batch.drop_constraint("source_confidence_range", type_="check")
        batch.drop_constraint("uq_cafes_overture_id", type_="unique")
        batch.drop_column("external_links_json")
        batch.drop_column("source_json")
        batch.drop_column("website")
        batch.drop_column("primary_category")
        batch.drop_column("source_confidence")
        batch.drop_column("source_release")
        batch.drop_column("overture_id")
        batch.alter_column("kakao_place_id", existing_type=sa.String(64), nullable=False)
        batch.create_index("ix_cafes_neighborhood_active", ["neighborhood", "active"])
