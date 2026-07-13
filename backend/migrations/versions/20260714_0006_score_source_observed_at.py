"""Materialize score source observation time for precomputed-only serving.

Revision ID: 20260714_0006
Revises: 20260713_0005
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260714_0006"
down_revision: str | None = "20260713_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
NULLABLE_JSON_VALUE = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True), "postgresql"
)


def upgrade() -> None:
    with op.batch_alter_table("cafe_scores") as batch:
        batch.add_column(
            sa.Column(
                "source_observed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    op.execute(
        sa.text(
            "UPDATE cafe_scores SET source_observed_at = ("
            "SELECT MAX(hotspot_snapshots.observed_at) "
            "FROM hotspot_snapshots "
            "WHERE hotspot_snapshots.hotspot_id = "
            "cafe_scores.primary_hotspot_id"
            ") WHERE coverage IN ('covered', 'fringe')"
        )
    )

    if not context.is_offline_mode():
        missing_source_count = op.get_bind().execute(
            sa.text(
                "SELECT count(*) FROM cafe_scores "
                "WHERE coverage IN ('covered', 'fringe') "
                "AND source_observed_at IS NULL"
            )
        ).scalar_one()
        if missing_source_count:
            raise RuntimeError(
                "cannot backfill source_observed_at for "
                f"{missing_source_count} covered cafe score(s)"
            )

    with op.batch_alter_table("cafe_scores") as batch:
        batch.create_check_constraint(
            "source_observed_matches_coverage",
            "(coverage = 'uncovered' AND source_observed_at IS NULL) OR "
            "(coverage IN ('covered', 'fringe') "
            "AND source_observed_at IS NOT NULL)",
        )

    op.create_table(
        "hotspot_serving_states",
        sa.Column("hotspot_id", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("trend_12h_json", JSON_VALUE, nullable=False),
        sa.Column(
            "forecast_1h_json", NULLABLE_JSON_VALUE, nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["hotspot_id"],
            ["hotspots.id"],
            name=op.f(
                "fk_hotspot_serving_states_hotspot_id_hotspots"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "hotspot_id", name=op.f("pk_hotspot_serving_states")
        ),
    )


def downgrade() -> None:
    op.drop_table("hotspot_serving_states")
    with op.batch_alter_table("cafe_scores") as batch:
        batch.drop_constraint(
            "source_observed_matches_coverage", type_="check"
        )
        batch.drop_column("source_observed_at")
