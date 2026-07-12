"""Deterministic polygon-based challenger for offline shadow evaluation.

The public ``v1-idw-point`` materialization remains authoritative.  This module
accepts already-normalized official WGS84 polygons and never performs I/O or
writes scores.  Cafes covered by one or more polygons use every covering area;
outside cafes use distance to polygon boundaries rather than representative
points.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import cos, exp, floor, radians
from typing import Literal, Mapping, Sequence

from shapely.affinity import scale
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from app.config import (
    POLYGON_SHADOW_CONF_HIGH,
    POLYGON_SHADOW_CONF_MID,
    POLYGON_SHADOW_COVERED_M,
    POLYGON_SHADOW_D_FLOOR_M,
    POLYGON_SHADOW_K_NEIGHBORS,
    POLYGON_SHADOW_MODEL_VERSION,
    POLYGON_SHADOW_R_MAX_M,
    POLYGON_SHADOW_TAU_MIN,
)
from app.geo import haversine_m
from app.scoring.engine import CafeEstimate, Contributor, HotspotObservation


Coverage = Literal["covered", "fringe", "uncovered"]
ConfidenceTier = Literal["high", "mid", "low"]
SelectionMode = Literal["inside", "boundary", "uncovered"]
GeometryNormalization = Literal["original", "make_valid"]


@dataclass(frozen=True, slots=True)
class PolygonHotspotObservation:
    hotspot_id: int
    area_cd: str
    name: str
    geometry_version: str
    geometry_normalization: GeometryNormalization
    geometry: BaseGeometry
    level: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PolygonHotspotGeometry:
    """Static verified geometry bound to one database hotspot id."""

    area_cd: str
    name: str
    geometry_version: str
    geometry_normalization: GeometryNormalization
    geometry: BaseGeometry


@dataclass(frozen=True, slots=True)
class PolygonContributor:
    hotspot_id: int
    area_cd: str
    name: str
    geometry_version: str
    geometry_normalization: GeometryNormalization
    inside_polygon: bool
    boundary_distance_m: float
    level: int
    observed_at: datetime
    weight: float


@dataclass(frozen=True, slots=True)
class PolygonCafeEstimate:
    model_version: str
    coverage: Coverage
    selection_mode: SelectionMode
    score: float | None
    level: int | None
    confidence: float | None
    confidence_tier: ConfidenceTier | None
    nearest_boundary_distance_m: float | None
    overlap_area_codes: tuple[str, ...]
    contributors: tuple[PolygonContributor, ...] | None


def _validate_geo_parameters(
    *,
    r_max_m: float,
    covered_m: float,
    k_neighbors: int,
    d_floor_m: float,
    tau_min: float,
) -> None:
    """Validate the spatial/freshness parameters shared by every shadow model."""

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
    _validate_geo_parameters(
        r_max_m=r_max_m,
        covered_m=covered_m,
        k_neighbors=k_neighbors,
        d_floor_m=d_floor_m,
        tau_min=tau_min,
    )
    if not 0 <= conf_mid <= conf_high <= 1:
        raise ValueError("confidence thresholds must satisfy 0 <= mid <= high <= 1")


def _confidence_tier(
    confidence: float, *, high: float, mid: float
) -> ConfidenceTier:
    if confidence >= high:
        return "high"
    if confidence >= mid:
        return "mid"
    return "low"


def _validate_geometry(geometry: BaseGeometry) -> None:
    """Reject anything that is not a normalized valid WGS84 Polygon geometry."""

    if not isinstance(geometry, BaseGeometry):
        raise ValueError("geometry must be a Shapely geometry")
    if (
        geometry.geom_type not in {"Polygon", "MultiPolygon"}
        or geometry.is_empty
        or not geometry.is_valid
    ):
        raise ValueError("geometry must be a normalized valid Polygon or MultiPolygon")
    min_lng, min_lat, max_lng, max_lat = geometry.bounds
    for name, value, lower, upper in (
        ("min_lng", min_lng, -180.0, 180.0),
        ("max_lng", max_lng, -180.0, 180.0),
        ("min_lat", min_lat, -90.0, 90.0),
        ("max_lat", max_lat, -90.0, 90.0),
    ):
        if not lower <= value <= upper:
            raise ValueError(f"geometry {name} is outside WGS84 bounds")


def _validate_observation(observation: PolygonHotspotObservation) -> None:
    if observation.level not in (1, 2, 3, 4):
        raise ValueError("observation level must be between 1 and 4")
    if observation.observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if not observation.area_cd or not observation.name:
        raise ValueError("area_cd and name must be non-empty")
    if not observation.geometry_version:
        raise ValueError("geometry_version must be non-empty")
    if observation.geometry_normalization not in ("original", "make_valid"):
        raise ValueError("unsupported geometry_normalization")
    _validate_geometry(observation.geometry)


def _boundary_distance_m(
    cafe_lat: float,
    cafe_lng: float,
    point: Point,
    geometry: BaseGeometry,
) -> float:
    if geometry.covers(point):
        return 0.0
    # GEOS treats WGS84 degrees as a flat Cartesian plane. At Seoul's latitude
    # that overstates east-west distance and can select the wrong boundary
    # segment. Scale longitude around the cafe for local equirectangular
    # nearest-point selection, then report the final great-circle distance.
    longitude_scale = cos(radians(cafe_lat))
    if cafe_lat in (-90.0, 90.0):
        raise ValueError("polygon boundary distance is undefined at the poles")
    local_geometry = scale(
        geometry,
        xfact=longitude_scale,
        yfact=1.0,
        origin=(cafe_lng, cafe_lat),
    )
    _, local_boundary_point = nearest_points(point, local_geometry.boundary)
    boundary_lng = cafe_lng + (
        local_boundary_point.x - cafe_lng
    ) / longitude_scale
    return haversine_m(
        cafe_lat,
        cafe_lng,
        local_boundary_point.y,
        boundary_lng,
    )


def score_cafe_polygon_shadow(
    cafe_lat: float,
    cafe_lng: float,
    observations: Sequence[PolygonHotspotObservation],
    *,
    now: datetime,
    r_max_m: float = POLYGON_SHADOW_R_MAX_M,
    covered_m: float = POLYGON_SHADOW_COVERED_M,
    k_neighbors: int = POLYGON_SHADOW_K_NEIGHBORS,
    d_floor_m: float = POLYGON_SHADOW_D_FLOOR_M,
    tau_min: float = POLYGON_SHADOW_TAU_MIN,
    conf_high: float = POLYGON_SHADOW_CONF_HIGH,
    conf_mid: float = POLYGON_SHADOW_CONF_MID,
) -> PolygonCafeEstimate:
    """Score one cafe with official hotspot polygons and no persistence.

    All polygons covering the cafe are contributors, even when their count is
    greater than ``k_neighbors``.  This is deliberate: truncating an overlap
    would invent a winner.  When no polygon covers the cafe, the nearest
    ``k_neighbors`` boundaries inside ``r_max_m`` use the same IDW/freshness
    formula as v1 so the shadow comparison changes only the spatial model.
    """

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
    # Reuse the shared strict WGS84 validation before constructing a point.
    haversine_m(cafe_lat, cafe_lng, cafe_lat, cafe_lng)
    point = Point(cafe_lng, cafe_lat)

    seen_hotspot_ids: set[int] = set()
    seen_area_codes: set[str] = set()
    measured: list[tuple[float, bool, PolygonHotspotObservation]] = []
    for observation in observations:
        _validate_observation(observation)
        if observation.hotspot_id in seen_hotspot_ids:
            raise ValueError(f"duplicate hotspot_id: {observation.hotspot_id}")
        if observation.area_cd in seen_area_codes:
            raise ValueError(f"duplicate area_cd: {observation.area_cd}")
        seen_hotspot_ids.add(observation.hotspot_id)
        seen_area_codes.add(observation.area_cd)
        inside = observation.geometry.covers(point)
        measured.append(
            (
                _boundary_distance_m(
                    cafe_lat,
                    cafe_lng,
                    point,
                    observation.geometry,
                ),
                inside,
                observation,
            )
        )

    containing = sorted(
        (item for item in measured if item[1]),
        key=lambda item: (item[2].hotspot_id, item[2].area_cd),
    )
    nearest_distance = min((item[0] for item in measured), default=None)
    if containing:
        selected = containing
        selection_mode: SelectionMode = "inside"
        coverage: Coverage = "covered"
    else:
        nearby = sorted(
            (item for item in measured if item[0] <= r_max_m),
            key=lambda item: (item[0], item[2].hotspot_id, item[2].area_cd),
        )
        selected = nearby[:k_neighbors]
        if not selected:
            return PolygonCafeEstimate(
                model_version=POLYGON_SHADOW_MODEL_VERSION,
                coverage="uncovered",
                selection_mode="uncovered",
                score=None,
                level=None,
                confidence=None,
                confidence_tier=None,
                nearest_boundary_distance_m=nearest_distance,
                overlap_area_codes=(),
                contributors=None,
            )
        selection_mode = "boundary"
        coverage = "covered" if selected[0][0] <= covered_m else "fringe"

    raw_weights = [
        1.0 / max(distance_m, d_floor_m) ** 2
        for distance_m, _, _ in selected
    ]
    weight_sum = sum(raw_weights)
    score = sum(
        weight * observation.level
        for weight, (_, _, observation) in zip(
            raw_weights, selected, strict=True
        )
    ) / weight_sum
    level = min(4, max(1, floor(score + 0.5)))

    selected_nearest_distance = selected[0][0] if not containing else 0.0
    latest_observed_at = max(
        observation.observed_at for _, _, observation in selected
    )
    age_minutes = max(0.0, (now - latest_observed_at).total_seconds() / 60.0)
    freshness = exp(-age_minutes / tau_min)
    coverage_factor = min(
        1.0, max(0.0, 1.0 - selected_nearest_distance / r_max_m)
    )
    neighbor_factor = min(1.0, len(selected) / 2.0)
    confidence = coverage_factor * freshness * neighbor_factor

    contributors = tuple(
        PolygonContributor(
            hotspot_id=observation.hotspot_id,
            area_cd=observation.area_cd,
            name=observation.name,
            geometry_version=observation.geometry_version,
            geometry_normalization=observation.geometry_normalization,
            inside_polygon=inside,
            boundary_distance_m=distance_m,
            level=observation.level,
            observed_at=observation.observed_at,
            weight=raw_weight / weight_sum,
        )
        for raw_weight, (distance_m, inside, observation) in zip(
            raw_weights, selected, strict=True
        )
    )
    overlap_area_codes = (
        tuple(item[2].area_cd for item in containing)
        if len(containing) > 1
        else ()
    )
    return PolygonCafeEstimate(
        model_version=POLYGON_SHADOW_MODEL_VERSION,
        coverage=coverage,
        selection_mode=selection_mode,
        score=score,
        level=level,
        confidence=confidence,
        confidence_tier=_confidence_tier(
            confidence,
            high=conf_high,
            mid=conf_mid,
        ),
        nearest_boundary_distance_m=selected_nearest_distance,
        overlap_area_codes=overlap_area_codes,
        contributors=contributors,
    )


def score_cafe_polygon_shadow_compatible(
    cafe_lat: float,
    cafe_lng: float,
    observations: Sequence[HotspotObservation],
    geometries: Mapping[int, PolygonHotspotGeometry],
    *,
    now: datetime,
) -> CafeEstimate:
    """Adapt polygon shadow output to the common evaluator contract.

    Geometry-specific overlap and provenance remain available from
    :func:`score_cafe_polygon_shadow`; this adapter intentionally exposes only
    the fields consumed by the existing paired evaluator.
    """

    polygon_observations: list[PolygonHotspotObservation] = []
    for observation in observations:
        geometry = geometries.get(observation.hotspot_id)
        if geometry is None:
            raise ValueError(
                f"missing polygon geometry for hotspot_id={observation.hotspot_id}"
            )
        if geometry.name != observation.name:
            raise ValueError(
                f"polygon/observation name mismatch for hotspot_id="
                f"{observation.hotspot_id}"
            )
        polygon_observations.append(
            PolygonHotspotObservation(
                hotspot_id=observation.hotspot_id,
                area_cd=geometry.area_cd,
                name=geometry.name,
                geometry_version=geometry.geometry_version,
                geometry_normalization=geometry.geometry_normalization,
                geometry=geometry.geometry,
                level=observation.level,
                observed_at=observation.observed_at,
            )
        )

    estimate = score_cafe_polygon_shadow(
        cafe_lat,
        cafe_lng,
        polygon_observations,
        now=now,
    )
    if estimate.contributors is None:
        return CafeEstimate(
            coverage=estimate.coverage,
            score=None,
            level=None,
            confidence=None,
            confidence_tier=None,
            primary_hotspot_id=None,
            primary_distance_m=None,
            contributors=None,
        )

    contributors = tuple(
        Contributor(
            hotspot_id=item.hotspot_id,
            name=item.name,
            distance_m=item.boundary_distance_m,
            level=item.level,
            observed_at=item.observed_at,
            weight=item.weight,
        )
        for item in estimate.contributors
    )
    primary = contributors[0]
    return CafeEstimate(
        coverage=estimate.coverage,
        score=estimate.score,
        level=estimate.level,
        confidence=estimate.confidence,
        confidence_tier=estimate.confidence_tier,
        primary_hotspot_id=primary.hotspot_id,
        primary_distance_m=primary.distance_m,
        contributors=contributors,
    )
