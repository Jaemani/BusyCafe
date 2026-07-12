"""Pure deterministic scoring functions for regional cafe congestion.

The score describes the area around a cafe, never seat occupancy of the cafe.
Persistence and upstream snapshot selection intentionally live outside this
module so fixed inputs always produce fixed outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp, floor
from typing import Literal, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.config import (
    CONF_HIGH,
    CONF_MID,
    COVERED_M,
    D_FLOOR_M,
    K_NEIGHBORS,
    R_MAX_M,
    SCORING_MODEL_VERSION,
    TAU_MIN,
)
from app.geo import haversine_m
from app.models import Cafe, CafeScore, Hotspot, HotspotSnapshot


Coverage = Literal["covered", "fringe", "uncovered"]
ConfidenceTier = Literal["high", "mid", "low"]


@dataclass(frozen=True, slots=True)
class HotspotObservation:
    hotspot_id: int
    name: str
    lat: float
    lng: float
    level: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class Contributor:
    hotspot_id: int
    name: str
    distance_m: float
    level: int
    observed_at: datetime
    weight: float


@dataclass(frozen=True, slots=True)
class CafeEstimate:
    coverage: Coverage
    score: float | None
    level: int | None
    confidence: float | None
    confidence_tier: ConfidenceTier | None
    primary_hotspot_id: int | None
    primary_distance_m: float | None
    contributors: tuple[Contributor, ...] | None


@dataclass(frozen=True, slots=True)
class MaterializeReport:
    cafes: int
    covered: int
    fringe: int
    uncovered: int


def _validate_parameters(
    *,
    r_max_m: float,
    covered_m: float,
    k_neighbors: int,
    d_floor_m: float,
    tau_min: float,
    conf_high: float,
    conf_mid: float,
) -> None:
    if r_max_m <= 0:
        raise ValueError("r_max_m must be positive")
    if not 0 <= covered_m <= r_max_m:
        raise ValueError("covered_m must be between zero and r_max_m")
    if k_neighbors < 1:
        raise ValueError("k_neighbors must be positive")
    if d_floor_m <= 0:
        raise ValueError("d_floor_m must be positive")
    if tau_min <= 0:
        raise ValueError("tau_min must be positive")
    if not 0 <= conf_mid <= conf_high <= 1:
        raise ValueError("confidence thresholds must satisfy 0 <= mid <= high <= 1")


def _confidence_tier(
    confidence: float,
    *,
    high: float,
    mid: float,
) -> ConfidenceTier:
    if confidence >= high:
        return "high"
    if confidence >= mid:
        return "mid"
    return "low"


def score_cafe(
    cafe_lat: float,
    cafe_lng: float,
    observations: Sequence[HotspotObservation],
    *,
    now: datetime,
    r_max_m: float = R_MAX_M,
    covered_m: float = COVERED_M,
    k_neighbors: int = K_NEIGHBORS,
    d_floor_m: float = D_FLOOR_M,
    tau_min: float = TAU_MIN,
    conf_high: float = CONF_HIGH,
    conf_mid: float = CONF_MID,
) -> CafeEstimate:
    """Estimate surrounding congestion from nearby latest hotspot snapshots."""

    _validate_parameters(
        r_max_m=r_max_m,
        covered_m=covered_m,
        k_neighbors=k_neighbors,
        d_floor_m=d_floor_m,
        tau_min=tau_min,
        conf_high=conf_high,
        conf_mid=conf_mid,
    )
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    neighbors: list[tuple[float, HotspotObservation]] = []
    for observation in observations:
        if observation.level not in (1, 2, 3, 4):
            raise ValueError("observation level must be between 1 and 4")
        if observation.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        distance_m = haversine_m(
            cafe_lat,
            cafe_lng,
            observation.lat,
            observation.lng,
        )
        if distance_m <= r_max_m:
            neighbors.append((distance_m, observation))

    neighbors.sort(key=lambda item: (item[0], item[1].hotspot_id))
    selected = neighbors[:k_neighbors]
    if not selected:
        return CafeEstimate(
            coverage="uncovered",
            score=None,
            level=None,
            confidence=None,
            confidence_tier=None,
            primary_hotspot_id=None,
            primary_distance_m=None,
            contributors=None,
        )

    raw_weights = [1.0 / max(distance, d_floor_m) ** 2 for distance, _ in selected]
    weight_sum = sum(raw_weights)
    score = sum(
        weight * observation.level
        for weight, (_, observation) in zip(raw_weights, selected, strict=True)
    ) / weight_sum
    # Product levels use conventional half-up rounding, not Python's bankers' round.
    level = min(4, max(1, floor(score + 0.5)))

    primary_distance_m, primary = selected[0]
    coverage: Coverage = "covered" if primary_distance_m <= covered_m else "fringe"
    latest_observed_at = max(observation.observed_at for _, observation in selected)
    age_minutes = max(0.0, (now - latest_observed_at).total_seconds() / 60.0)
    freshness = exp(-age_minutes / tau_min)
    coverage_factor = min(1.0, max(0.0, 1.0 - primary_distance_m / r_max_m))
    neighbor_factor = min(1.0, len(selected) / 2.0)
    confidence = coverage_factor * freshness * neighbor_factor

    contributors = tuple(
        Contributor(
            hotspot_id=observation.hotspot_id,
            name=observation.name,
            distance_m=distance,
            level=observation.level,
            observed_at=observation.observed_at,
            weight=raw_weight / weight_sum,
        )
        for raw_weight, (distance, observation) in zip(
            raw_weights, selected, strict=True
        )
    )
    return CafeEstimate(
        coverage=coverage,
        score=score,
        level=level,
        confidence=confidence,
        confidence_tier=_confidence_tier(
            confidence,
            high=conf_high,
            mid=conf_mid,
        ),
        primary_hotspot_id=primary.hotspot_id,
        primary_distance_m=primary_distance_m,
        contributors=contributors,
    )


def _database_datetime(value: datetime) -> datetime:
    """SQLite drops timezone metadata; the persistence contract is UTC."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def materialize_all(
    session: Session,
    *,
    now: datetime | None = None,
) -> MaterializeReport:
    """Upsert deterministic estimates for all active cached cafes."""

    computed_at = now or datetime.now(UTC)
    if computed_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    latest = (
        select(
            HotspotSnapshot.hotspot_id,
            func.max(HotspotSnapshot.observed_at).label("observed_at"),
        )
        .group_by(HotspotSnapshot.hotspot_id)
        .subquery()
    )
    rows = session.execute(
        select(
            Hotspot.id,
            Hotspot.name,
            Hotspot.lat,
            Hotspot.lng,
            HotspotSnapshot.congest_level,
            HotspotSnapshot.observed_at,
        )
        .join(latest, latest.c.hotspot_id == Hotspot.id)
        .join(
            HotspotSnapshot,
            (HotspotSnapshot.hotspot_id == latest.c.hotspot_id)
            & (HotspotSnapshot.observed_at == latest.c.observed_at),
        )
        .where(Hotspot.is_polled.is_(True))
        .order_by(Hotspot.id)
    ).all()
    observations = tuple(
        HotspotObservation(
            hotspot_id=hotspot_id,
            name=name,
            lat=lat,
            lng=lng,
            level=congest_level,
            observed_at=_database_datetime(observed_at),
        )
        for hotspot_id, name, lat, lng, congest_level, observed_at in rows
    )
    # Materialization only needs score identities and cafe coordinates. Avoid
    # transferring cached POI/source JSON and previous contributor JSON from a
    # remote PostgreSQL database on every ingest cycle.
    existing_scores = {
        item.cafe_id: item
        for item in session.scalars(
            select(CafeScore).options(load_only(CafeScore.cafe_id))
        )
    }
    cafes = session.execute(
        select(Cafe.id, Cafe.lat, Cafe.lng)
        .where(Cafe.active.is_(True))
        .order_by(Cafe.id)
    ).all()
    counts: dict[Coverage, int] = {"covered": 0, "fringe": 0, "uncovered": 0}
    for cafe_id, cafe_lat, cafe_lng in cafes:
        estimate = score_cafe(
            cafe_lat,
            cafe_lng,
            observations,
            now=computed_at,
        )
        counts[estimate.coverage] += 1
        values = {
            "model_version": SCORING_MODEL_VERSION,
            "computed_at": computed_at,
            "score": estimate.score,
            "level": estimate.level,
            "confidence": estimate.confidence,
            "confidence_tier": estimate.confidence_tier,
            "coverage": estimate.coverage,
            "primary_hotspot_id": estimate.primary_hotspot_id,
            "primary_distance_m": estimate.primary_distance_m,
            "contributors_json": (
                [
                    {
                        "hotspot_id": contributor.hotspot_id,
                        "distance_m": contributor.distance_m,
                        "level": contributor.level,
                        "weight": contributor.weight,
                    }
                    for contributor in estimate.contributors
                ]
                if estimate.contributors is not None
                else None
            ),
        }
        existing = existing_scores.get(cafe_id)
        if existing is None:
            session.add(CafeScore(cafe_id=cafe_id, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
    session.commit()
    return MaterializeReport(
        cafes=len(cafes),
        covered=counts["covered"],
        fringe=counts["fringe"],
        uncovered=counts["uncovered"],
    )
