"""Deterministic population-density challenger for offline shadow evaluation.

This ``v3-density-shadow`` model reuses the v2 polygon spatial selection but
replaces the discrete 1-4 congestion label with population density
(people/m^2) as the interpolated signal.  Density spans several orders of
magnitude across Seoul hotspots, so interpolation happens in log space and the
estimate is reported as a continuous density rather than a level.

There is deliberately **no 1-4 level mapping** here: the density-to-level
thresholds do not exist until a calibrated baseline (Track 1) provides real
cut points.  Emitting an invented level would imply an accuracy the model has
not earned, so this module reports only ``density_per_m2`` and confidence.

Like :mod:`app.scoring.polygon_shadow`, this module accepts already-normalized
official WGS84 polygons, performs no I/O, and never writes scores.  Polygon
area uses the same local equirectangular approximation as
``polygon_shadow._boundary_distance_m`` so the two challengers share one
projection convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import cos, exp, log, radians
from typing import Sequence

from shapely.affinity import scale
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from app.config import (
    DENSITY_SHADOW_AREA_MAX_M2,
    DENSITY_SHADOW_AREA_MIN_M2,
    DENSITY_SHADOW_COVERED_M,
    DENSITY_SHADOW_D_FLOOR_M,
    DENSITY_SHADOW_K_NEIGHBORS,
    DENSITY_SHADOW_LOG_EPSILON,
    DENSITY_SHADOW_MODEL_VERSION,
    DENSITY_SHADOW_R_MAX_M,
    DENSITY_SHADOW_TAU_MIN,
    M_PER_DEG_LAT,
)
from app.geo import haversine_m
from app.scoring.polygon_shadow import (
    Coverage,
    GeometryNormalization,
    SelectionMode,
    _boundary_distance_m,
    _validate_geo_parameters,
    _validate_geometry,
)


@dataclass(frozen=True, slots=True)
class DensityHotspotObservation:
    hotspot_id: int
    area_cd: str
    name: str
    geometry_version: str
    geometry_normalization: GeometryNormalization
    geometry: BaseGeometry
    ppltn_min: int | None
    ppltn_max: int | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DensityContributor:
    hotspot_id: int
    area_cd: str
    name: str
    geometry_version: str
    geometry_normalization: GeometryNormalization
    inside_polygon: bool
    boundary_distance_m: float
    density_per_m2: float
    area_m2: float
    observed_at: datetime
    weight: float


@dataclass(frozen=True, slots=True)
class DensityCafeEstimate:
    model_version: str
    coverage: Coverage
    selection_mode: SelectionMode
    density_per_m2: float | None
    confidence: float | None
    nearest_boundary_distance_m: float | None
    overlap_area_codes: tuple[str, ...]
    excluded_missing_ppltn: int
    contributors: tuple[DensityContributor, ...] | None


def _polygon_area_m2(geometry: BaseGeometry) -> float:
    """Approximate a WGS84 polygon area in square metres.

    Scales longitude by ``cos(centroid latitude)`` about the polygon centroid so
    a degree of longitude and a degree of latitude cover comparable ground
    distance, takes the planar area in scaled-degrees^2, and converts with
    :data:`M_PER_DEG_LAT`.  This mirrors the local equirectangular convention of
    ``polygon_shadow._boundary_distance_m``.
    """

    centroid = geometry.centroid
    longitude_scale = cos(radians(centroid.y))
    local_geometry = scale(
        geometry,
        xfact=longitude_scale,
        yfact=1.0,
        origin=(centroid.x, centroid.y),
    )
    area_m2 = local_geometry.area * M_PER_DEG_LAT**2
    if not DENSITY_SHADOW_AREA_MIN_M2 <= area_m2 <= DENSITY_SHADOW_AREA_MAX_M2:
        raise ValueError(
            f"polygon area {area_m2:.1f} m^2 is outside the supported range "
            f"[{DENSITY_SHADOW_AREA_MIN_M2:g}, {DENSITY_SHADOW_AREA_MAX_M2:g}]"
        )
    return area_m2


def _validate_observation(observation: DensityHotspotObservation) -> None:
    if observation.observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if not observation.area_cd or not observation.name:
        raise ValueError("area_cd and name must be non-empty")
    if not observation.geometry_version:
        raise ValueError("geometry_version must be non-empty")
    if observation.geometry_normalization not in ("original", "make_valid"):
        raise ValueError("unsupported geometry_normalization")
    _validate_geometry(observation.geometry)


def score_cafe_density_shadow(
    cafe_lat: float,
    cafe_lng: float,
    observations: Sequence[DensityHotspotObservation],
    *,
    now: datetime,
    r_max_m: float = DENSITY_SHADOW_R_MAX_M,
    covered_m: float = DENSITY_SHADOW_COVERED_M,
    k_neighbors: int = DENSITY_SHADOW_K_NEIGHBORS,
    d_floor_m: float = DENSITY_SHADOW_D_FLOOR_M,
    tau_min: float = DENSITY_SHADOW_TAU_MIN,
    log_epsilon: float = DENSITY_SHADOW_LOG_EPSILON,
) -> DensityCafeEstimate:
    """Estimate surrounding population density from official hotspot polygons.

    Spatial selection is identical to
    :func:`app.scoring.polygon_shadow.score_cafe_polygon_shadow`: every polygon
    covering the cafe contributes (``inside`` mode); otherwise the nearest
    ``k_neighbors`` boundaries within ``r_max_m`` contribute (``boundary``
    mode); otherwise the cafe is ``uncovered``.  Observations whose ``ppltn_min``
    or ``ppltn_max`` is ``None`` cannot yield a density, so they are excluded
    from scoring but counted in ``excluded_missing_ppltn``.

    Density is interpolated in log space with the same inverse-distance-squared
    weights and freshness/coverage confidence as the polygon model.
    """

    _validate_geo_parameters(
        r_max_m=r_max_m,
        covered_m=covered_m,
        k_neighbors=k_neighbors,
        d_floor_m=d_floor_m,
        tau_min=tau_min,
    )
    if log_epsilon <= 0:
        raise ValueError("log_epsilon must be positive")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    # Reuse the shared strict WGS84 validation before constructing a point.
    haversine_m(cafe_lat, cafe_lng, cafe_lat, cafe_lng)
    point = Point(cafe_lng, cafe_lat)

    seen_hotspot_ids: set[int] = set()
    seen_area_codes: set[str] = set()
    excluded_missing_ppltn = 0
    # (boundary_distance_m, inside, observation, density_per_m2, area_m2)
    measured: list[
        tuple[float, bool, DensityHotspotObservation, float, float]
    ] = []
    for observation in observations:
        _validate_observation(observation)
        if observation.hotspot_id in seen_hotspot_ids:
            raise ValueError(f"duplicate hotspot_id: {observation.hotspot_id}")
        if observation.area_cd in seen_area_codes:
            raise ValueError(f"duplicate area_cd: {observation.area_cd}")
        seen_hotspot_ids.add(observation.hotspot_id)
        seen_area_codes.add(observation.area_cd)
        if observation.ppltn_min is None or observation.ppltn_max is None:
            excluded_missing_ppltn += 1
            continue
        if observation.ppltn_min < 0 or observation.ppltn_max < 0:
            raise ValueError("ppltn values must be non-negative")
        if observation.ppltn_min > observation.ppltn_max:
            raise ValueError("ppltn_min must not exceed ppltn_max")
        area_m2 = _polygon_area_m2(observation.geometry)
        density = ((observation.ppltn_min + observation.ppltn_max) / 2.0) / area_m2
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
                density,
                area_m2,
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
            return DensityCafeEstimate(
                model_version=DENSITY_SHADOW_MODEL_VERSION,
                coverage="uncovered",
                selection_mode="uncovered",
                density_per_m2=None,
                confidence=None,
                nearest_boundary_distance_m=nearest_distance,
                overlap_area_codes=(),
                excluded_missing_ppltn=excluded_missing_ppltn,
                contributors=None,
            )
        selection_mode = "boundary"
        coverage = "covered" if selected[0][0] <= covered_m else "fringe"

    raw_weights = [
        1.0 / max(distance_m, d_floor_m) ** 2
        for distance_m, _, _, _, _ in selected
    ]
    weight_sum = sum(raw_weights)
    # Interpolate ln(density + epsilon): density spans orders of magnitude and a
    # genuinely empty hotspot (density == 0) must map to a finite floor, not -inf.
    weighted_log_density = sum(
        weight * log(density + log_epsilon)
        for weight, (_, _, _, density, _) in zip(
            raw_weights, selected, strict=True
        )
    ) / weight_sum
    density_per_m2 = exp(weighted_log_density)

    selected_nearest_distance = selected[0][0] if not containing else 0.0
    latest_observed_at = max(
        observation.observed_at for _, _, observation, _, _ in selected
    )
    age_minutes = max(0.0, (now - latest_observed_at).total_seconds() / 60.0)
    freshness = exp(-age_minutes / tau_min)
    coverage_factor = min(
        1.0, max(0.0, 1.0 - selected_nearest_distance / r_max_m)
    )
    neighbor_factor = min(1.0, len(selected) / 2.0)
    confidence = coverage_factor * freshness * neighbor_factor

    contributors = tuple(
        DensityContributor(
            hotspot_id=observation.hotspot_id,
            area_cd=observation.area_cd,
            name=observation.name,
            geometry_version=observation.geometry_version,
            geometry_normalization=observation.geometry_normalization,
            inside_polygon=inside,
            boundary_distance_m=distance_m,
            density_per_m2=density,
            area_m2=area_m2,
            observed_at=observation.observed_at,
            weight=raw_weight / weight_sum,
        )
        for raw_weight, (distance_m, inside, observation, density, area_m2) in zip(
            raw_weights, selected, strict=True
        )
    )
    overlap_area_codes = (
        tuple(item[2].area_cd for item in containing)
        if len(containing) > 1
        else ()
    )
    return DensityCafeEstimate(
        model_version=DENSITY_SHADOW_MODEL_VERSION,
        coverage=coverage,
        selection_mode=selection_mode,
        density_per_m2=density_per_m2,
        confidence=confidence,
        nearest_boundary_distance_m=selected_nearest_distance,
        overlap_area_codes=overlap_area_codes,
        excluded_missing_ppltn=excluded_missing_ppltn,
        contributors=contributors,
    )
