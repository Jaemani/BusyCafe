"""Create hotspot, snapshot, cafe, and materialized score tables.

Revision ID: 20260711_0001
Revises:
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260711_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "hotspots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("area_cd", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column(
            "is_polled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.CheckConstraint("lat BETWEEN -90 AND 90", name=op.f("ck_hotspots_lat_range")),
        sa.CheckConstraint(
            "lng BETWEEN -180 AND 180", name=op.f("ck_hotspots_lng_range")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hotspots"),
        sa.UniqueConstraint("area_cd", name="uq_hotspots_area_cd"),
    )
    op.create_index("ix_hotspots_is_polled", "hotspots", ["is_polled"])

    op.create_table(
        "cafes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kakao_place_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("road_address", sa.String(length=500), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("place_url", sa.String(length=500), nullable=True),
        sa.Column("neighborhood", sa.String(length=64), nullable=True),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.CheckConstraint("lat BETWEEN -90 AND 90", name=op.f("ck_cafes_lat_range")),
        sa.CheckConstraint("lng BETWEEN -180 AND 180", name=op.f("ck_cafes_lng_range")),
        sa.PrimaryKeyConstraint("id", name="pk_cafes"),
        sa.UniqueConstraint("kakao_place_id", name="uq_cafes_kakao_place_id"),
    )
    op.create_index("ix_cafes_bbox", "cafes", ["lng", "lat"])
    op.create_index(
        "ix_cafes_neighborhood_active", "cafes", ["neighborhood", "active"]
    )

    op.create_table(
        "hotspot_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hotspot_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("congest_level", sa.Integer(), nullable=False),
        sa.Column("congest_label", sa.String(length=32), nullable=False),
        sa.Column("ppltn_min", sa.Integer(), nullable=True),
        sa.Column("ppltn_max", sa.Integer(), nullable=True),
        sa.Column("forecast_json", JSON_VALUE, nullable=True),
        sa.Column("raw_json", JSON_VALUE, nullable=True),
        sa.CheckConstraint(
            "congest_level BETWEEN 1 AND 4",
            name=op.f("ck_hotspot_snapshots_congest_level_range"),
        ),
        sa.CheckConstraint(
            "ppltn_min IS NULL OR ppltn_min >= 0",
            name=op.f("ck_hotspot_snapshots_ppltn_min_nonnegative"),
        ),
        sa.CheckConstraint(
            "ppltn_max IS NULL OR ppltn_max >= 0",
            name=op.f("ck_hotspot_snapshots_ppltn_max_nonnegative"),
        ),
        sa.CheckConstraint(
            "ppltn_min IS NULL OR ppltn_max IS NULL OR ppltn_min <= ppltn_max",
            name=op.f("ck_hotspot_snapshots_ppltn_bounds_order"),
        ),
        sa.ForeignKeyConstraint(
            ["hotspot_id"], ["hotspots.id"],
            name="fk_hotspot_snapshots_hotspot_id_hotspots", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hotspot_snapshots"),
        sa.UniqueConstraint(
            "hotspot_id", "observed_at", name="uq_snapshot_hotspot_observed"
        ),
    )
    op.create_index(
        "ix_snap_hotspot_time",
        "hotspot_snapshots",
        ["hotspot_id", sa.text("observed_at DESC")],
    )

    op.create_table(
        "hotspot_parse_failures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hotspot_id", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_type", sa.String(length=255), nullable=False),
        sa.Column("error_message", sa.String(length=1000), nullable=False),
        sa.Column("raw_json", JSON_VALUE, nullable=False),
        sa.CheckConstraint(
            "length(error_type) > 0",
            name=op.f("ck_hotspot_parse_failures_error_type_nonempty"),
        ),
        sa.CheckConstraint(
            "length(error_message) > 0",
            name=op.f("ck_hotspot_parse_failures_error_message_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["hotspot_id"],
            ["hotspots.id"],
            name="fk_hotspot_parse_failures_hotspot_id_hotspots",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hotspot_parse_failures"),
    )
    op.create_index(
        "ix_parse_failure_hotspot_time",
        "hotspot_parse_failures",
        ["hotspot_id", sa.text("fetched_at DESC")],
    )

    op.create_table(
        "cafe_scores",
        sa.Column("cafe_id", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confidence_tier", sa.String(length=16), nullable=True),
        sa.Column("coverage", sa.String(length=16), nullable=False),
        sa.Column("primary_hotspot_id", sa.Integer(), nullable=True),
        sa.Column("primary_distance_m", sa.Float(), nullable=True),
        sa.Column("contributors_json", JSON_VALUE, nullable=True),
        sa.CheckConstraint(
            "coverage IN ('covered', 'fringe', 'uncovered')",
            name=op.f("ck_cafe_scores_coverage_values"),
        ),
        sa.CheckConstraint(
            "confidence_tier IS NULL OR confidence_tier IN ('high', 'mid', 'low')",
            name=op.f("ck_cafe_scores_confidence_tier_values"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 1.0 AND 4.0",
            name=op.f("ck_cafe_scores_score_range"),
        ),
        sa.CheckConstraint(
            "level IS NULL OR level BETWEEN 1 AND 4",
            name=op.f("ck_cafe_scores_level_range"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0",
            name=op.f("ck_cafe_scores_confidence_range"),
        ),
        sa.CheckConstraint(
            "primary_distance_m IS NULL OR primary_distance_m >= 0",
            name=op.f("ck_cafe_scores_primary_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "(coverage = 'uncovered' AND score IS NULL AND level IS NULL "
            "AND confidence IS NULL AND confidence_tier IS NULL "
            "AND primary_hotspot_id IS NULL AND primary_distance_m IS NULL "
            "AND contributors_json IS NULL) OR "
            "(coverage IN ('covered', 'fringe') AND score IS NOT NULL "
            "AND level IS NOT NULL AND confidence IS NOT NULL "
            "AND confidence_tier IS NOT NULL AND primary_hotspot_id IS NOT NULL "
            "AND primary_distance_m IS NOT NULL AND contributors_json IS NOT NULL)",
            name=op.f("ck_cafe_scores_coverage_nullable_fields"),
        ),
        sa.ForeignKeyConstraint(
            ["cafe_id"], ["cafes.id"],
            name="fk_cafe_scores_cafe_id_cafes", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["primary_hotspot_id"], ["hotspots.id"],
            name="fk_cafe_scores_primary_hotspot_id_hotspots"
        ),
        sa.PrimaryKeyConstraint("cafe_id", name="pk_cafe_scores"),
    )
    op.create_index(
        "ix_cafe_scores_coverage_confidence",
        "cafe_scores",
        ["coverage", "confidence"],
    )


def downgrade() -> None:
    op.drop_index("ix_cafe_scores_coverage_confidence", table_name="cafe_scores")
    op.drop_table("cafe_scores")
    op.drop_index(
        "ix_parse_failure_hotspot_time", table_name="hotspot_parse_failures"
    )
    op.drop_table("hotspot_parse_failures")
    op.drop_index("ix_snap_hotspot_time", table_name="hotspot_snapshots")
    op.drop_table("hotspot_snapshots")
    op.drop_index("ix_cafes_neighborhood_active", table_name="cafes")
    op.drop_index("ix_cafes_bbox", table_name="cafes")
    op.drop_table("cafes")
    op.drop_index("ix_hotspots_is_polled", table_name="hotspots")
    op.drop_table("hotspots")
