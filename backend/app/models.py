"""SQLAlchemy persistence models and database-level invariants."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")
NULLABLE_JSON_VALUE = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True), "postgresql"
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IngestCycle(Base):
    """Durable operational result for one scheduled ingest cycle."""

    __tablename__ = "ingest_cycles"
    __table_args__ = (
        CheckConstraint("targets >= 0", name="targets_nonnegative"),
        CheckConstraint("saved >= 0", name="saved_nonnegative"),
        CheckConstraint("failed >= 0", name="failed_nonnegative"),
        CheckConstraint(
            "saved + failed <= targets", name="result_within_targets"
        ),
        CheckConstraint(
            "status IN ('running', 'complete', 'partial', 'failed')",
            name="status_values",
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status != 'running' AND completed_at IS NOT NULL)",
            name="completion_matches_status",
        ),
        CheckConstraint(
            "status != 'complete' OR "
            "(targets > 0 AND saved = targets AND failed = 0)",
            name="complete_result",
        ),
        Index("ix_ingest_cycles_started_at", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    targets: Mapped[int] = mapped_column(Integer, nullable=False)
    saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class Hotspot(Base):
    __tablename__ = "hotspots"
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90", name="lat_range"),
        CheckConstraint("lng BETWEEN -180 AND 180", name="lng_range"),
        Index("ix_hotspots_is_polled", "is_polled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_cd: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    is_polled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    snapshots: Mapped[list[HotspotSnapshot]] = relationship(
        back_populates="hotspot", cascade="all, delete-orphan", passive_deletes=True
    )
    parse_failures: Mapped[list[HotspotParseFailure]] = relationship(
        back_populates="hotspot", cascade="all, delete-orphan", passive_deletes=True
    )
    primary_scores: Mapped[list[CafeScore]] = relationship(
        back_populates="primary_hotspot"
    )
    serving_state: Mapped[HotspotServingState | None] = relationship(
        back_populates="hotspot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class HotspotSnapshot(Base):
    __tablename__ = "hotspot_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "hotspot_id", "observed_at", name="uq_snapshot_hotspot_observed"
        ),
        CheckConstraint(
            "congest_level BETWEEN 1 AND 4", name="congest_level_range"
        ),
        CheckConstraint(
            "ppltn_min IS NULL OR ppltn_min >= 0", name="ppltn_min_nonnegative"
        ),
        CheckConstraint(
            "ppltn_max IS NULL OR ppltn_max >= 0", name="ppltn_max_nonnegative"
        ),
        CheckConstraint(
            "ppltn_min IS NULL OR ppltn_max IS NULL OR ppltn_min <= ppltn_max",
            name="ppltn_bounds_order",
        ),
        Index(
            "ix_snap_hotspot_time",
            "hotspot_id",
            text("observed_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotspot_id: Mapped[int] = mapped_column(
        ForeignKey("hotspots.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    congest_level: Mapped[int] = mapped_column(Integer, nullable=False)
    congest_label: Mapped[str] = mapped_column(String(32), nullable=False)
    ppltn_min: Mapped[int | None] = mapped_column(Integer)
    ppltn_max: Mapped[int | None] = mapped_column(Integer)
    forecast_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_VALUE)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)

    hotspot: Mapped[Hotspot] = relationship(back_populates="snapshots")


class HotspotServingState(Base):
    """Precomputed detail evidence; API reads never scan raw snapshots."""

    __tablename__ = "hotspot_serving_states"

    hotspot_id: Mapped[int] = mapped_column(
        ForeignKey("hotspots.id", ondelete="CASCADE"), primary_key=True
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    trend_12h_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, nullable=False
    )
    forecast_1h_json: Mapped[dict[str, Any] | None] = mapped_column(
        NULLABLE_JSON_VALUE
    )

    hotspot: Mapped[Hotspot] = relationship(back_populates="serving_state")


class HotspotParseFailure(Base):
    """Append-only raw response retained when upstream schema parsing fails."""

    __tablename__ = "hotspot_parse_failures"
    __table_args__ = (
        CheckConstraint("length(error_type) > 0", name="error_type_nonempty"),
        CheckConstraint("length(error_message) > 0", name="error_message_nonempty"),
        Index(
            "ix_parse_failure_hotspot_time",
            "hotspot_id",
            text("fetched_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotspot_id: Mapped[int] = mapped_column(
        ForeignKey("hotspots.id", ondelete="CASCADE"), nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    error_type: Mapped[str] = mapped_column(String(255), nullable=False)
    error_message: Mapped[str] = mapped_column(String(1_000), nullable=False)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)

    hotspot: Mapped[Hotspot] = relationship(back_populates="parse_failures")


class Cafe(Base):
    __tablename__ = "cafes"
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90", name="lat_range"),
        CheckConstraint("lng BETWEEN -180 AND 180", name="lng_range"),
        CheckConstraint(
            "length(origin_provider) > 0", name="origin_provider_nonempty"
        ),
        CheckConstraint(
            "length(origin_source_id) > 0", name="origin_source_id_nonempty"
        ),
        UniqueConstraint(
            "origin_provider",
            "origin_source_id",
            name="uq_cafes_origin_provider_source_id",
        ),
        CheckConstraint(
            "source_confidence BETWEEN 0.0 AND 1.0", name="source_confidence_range"
        ),
        Index("ix_cafes_bbox", "lng", "lat"),
        Index("ix_cafes_active_bbox", "active", "lng", "lat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ``id`` is the provider-neutral canonical identity. Overture remains the
    # initial origin for existing rows, but provider aliases never replace the
    # canonical primary key used by scores and API responses.
    origin_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="overture", server_default="overture"
    )
    origin_source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    overture_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    source_release: Mapped[str] = mapped_column(String(32), nullable=False)
    source_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    primary_category: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    road_address: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(64))
    website: Mapped[str | None] = mapped_column(String(500))
    source_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_VALUE)
    external_links_json: Mapped[dict[str, str] | None] = mapped_column(JSON_VALUE)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    score: Mapped[CafeScore | None] = relationship(
        back_populates="cafe", cascade="all, delete-orphan", passive_deletes=True
    )
    provider_places: Mapped[list[CafeProviderPlace]] = relationship(
        back_populates="cafe", cascade="all, delete-orphan", passive_deletes=True
    )

    def __init__(self, **kwargs: Any) -> None:
        if not kwargs.get("origin_source_id") and kwargs.get("overture_id"):
            kwargs["origin_source_id"] = kwargs["overture_id"]
        super().__init__(**kwargs)


class CafeProviderPlace(Base):
    """One verified provider identity attached to a canonical cafe."""

    __tablename__ = "cafe_provider_places"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_place_id",
            name="uq_cafe_provider_places_provider_place_id",
        ),
        UniqueConstraint(
            "cafe_id",
            "provider",
            name="uq_cafe_provider_places_cafe_provider",
        ),
        CheckConstraint("length(provider) > 0", name="provider_nonempty"),
        CheckConstraint(
            "length(provider_place_id) > 0", name="provider_place_id_nonempty"
        ),
        CheckConstraint("length(match_method) > 0", name="match_method_nonempty"),
        CheckConstraint(
            "match_distance_m IS NULL OR match_distance_m >= 0",
            name="match_distance_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cafe_id: Mapped[int] = mapped_column(
        ForeignKey("cafes.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    detail_url: Mapped[str | None] = mapped_column(String(1_000))
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    match_method: Mapped[str] = mapped_column(String(64), nullable=False)
    match_distance_m: Mapped[float | None] = mapped_column(Float)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    cafe: Mapped[Cafe] = relationship(back_populates="provider_places")


class CafeScore(Base):
    __tablename__ = "cafe_scores"
    __table_args__ = (
        CheckConstraint(
            "coverage IN ('covered', 'fringe', 'uncovered')",
            name="coverage_values",
        ),
        CheckConstraint(
            "confidence_tier IS NULL OR confidence_tier IN ('high', 'mid', 'low')",
            name="confidence_tier_values",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 1.0 AND 4.0", name="score_range"
        ),
        CheckConstraint(
            "level IS NULL OR level BETWEEN 1 AND 4", name="level_range"
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0",
            name="confidence_range",
        ),
        CheckConstraint(
            "(coverage = 'uncovered' AND source_observed_at IS NULL) OR "
            "(coverage IN ('covered', 'fringe') "
            "AND source_observed_at IS NOT NULL)",
            name="source_observed_matches_coverage",
        ),
        CheckConstraint(
            "primary_distance_m IS NULL OR primary_distance_m >= 0",
            name="primary_distance_nonnegative",
        ),
        CheckConstraint(
            "(coverage = 'uncovered' AND score IS NULL AND level IS NULL "
            "AND confidence IS NULL AND confidence_tier IS NULL "
            "AND primary_hotspot_id IS NULL AND primary_distance_m IS NULL "
            "AND contributors_json IS NULL) OR "
            "(coverage IN ('covered', 'fringe') AND score IS NOT NULL "
            "AND level IS NOT NULL AND confidence IS NOT NULL "
            "AND confidence_tier IS NOT NULL AND primary_hotspot_id IS NOT NULL "
            "AND primary_distance_m IS NOT NULL AND contributors_json IS NOT NULL)",
            name="coverage_nullable_fields",
        ),
        Index("ix_cafe_scores_coverage_confidence", "coverage", "confidence"),
    )

    cafe_id: Mapped[int] = mapped_column(
        ForeignKey("cafes.id", ondelete="CASCADE"), primary_key=True
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    score: Mapped[float | None] = mapped_column(Float)
    level: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_tier: Mapped[str | None] = mapped_column(String(16))
    coverage: Mapped[str] = mapped_column(String(16), nullable=False)
    primary_hotspot_id: Mapped[int | None] = mapped_column(
        ForeignKey("hotspots.id"), nullable=True
    )
    primary_distance_m: Mapped[float | None] = mapped_column(Float)
    contributors_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        NULLABLE_JSON_VALUE
    )

    cafe: Mapped[Cafe] = relationship(back_populates="score")
    primary_hotspot: Mapped[Hotspot | None] = relationship(
        back_populates="primary_scores"
    )
