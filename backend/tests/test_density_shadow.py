from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import exp, log

import pytest
from shapely.geometry import Polygon, box

from app.config import DENSITY_SHADOW_LOG_EPSILON
from app.scoring.density_shadow import (
    DensityHotspotObservation,
    _polygon_area_m2,
    score_cafe_density_shadow,
)
from app.geo import haversine_m


NOW = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


def observation(
    hotspot_id: int,
    geometry,
    *,
    ppltn_min: int | None = 1500,
    ppltn_max: int | None = 2500,
    age_minutes: float = 0,
    normalization: str = "original",
) -> DensityHotspotObservation:
    return DensityHotspotObservation(
        hotspot_id=hotspot_id,
        area_cd=f"POI{hotspot_id:03d}",
        name=f"핫스팟 {hotspot_id}",
        geometry_version="fixture-v1",
        geometry_normalization=normalization,
        geometry=geometry,
        ppltn_min=ppltn_min,
        ppltn_max=ppltn_max,
        observed_at=NOW - timedelta(minutes=age_minutes),
    )


def _density(observation: DensityHotspotObservation) -> float:
    mean = (observation.ppltn_min + observation.ppltn_max) / 2.0
    return mean / _polygon_area_m2(observation.geometry)


def test_polygon_area_matches_haversine_side_lengths_near_seoul() -> None:
    # A roughly 300m square centred near Seoul (lat 37.55).
    lat, lng = 37.55, 127.0
    half_dlat, half_dlng = 0.00135, 0.0017
    square = box(lng - half_dlng, lat - half_dlat, lng + half_dlng, lat + half_dlat)

    east_west_m = haversine_m(lat, lng - half_dlng, lat, lng + half_dlng)
    north_south_m = haversine_m(lat - half_dlat, lng, lat + half_dlat, lng)
    expected_area_m2 = east_west_m * north_south_m

    assert _polygon_area_m2(square) == pytest.approx(expected_area_m2, rel=0.02)


def test_log_density_interpolation_is_weighted_geometric_mean_of_densities() -> None:
    # Two polygons cover the cafe with equal boundary distance, so the log-space
    # IDW reduces to the geometric mean of their densities.
    smaller = observation(1, box(-0.001, -0.001, 0.001, 0.001))
    larger = observation(2, box(-0.002, -0.002, 0.002, 0.002))
    density_small = _density(smaller)
    density_large = _density(larger)
    expected = exp(
        0.5 * log(density_small + DENSITY_SHADOW_LOG_EPSILON)
        + 0.5 * log(density_large + DENSITY_SHADOW_LOG_EPSILON)
    )

    result = score_cafe_density_shadow(0.0, 0.0, [larger, smaller], now=NOW)

    assert result.selection_mode == "inside"
    assert result.coverage == "covered"
    assert result.density_per_m2 == pytest.approx(expected)
    assert result.overlap_area_codes == ("POI001", "POI002")
    assert result.excluded_missing_ppltn == 0
    assert result.contributors is not None
    assert [item.hotspot_id for item in result.contributors] == [1, 2]
    assert [item.weight for item in result.contributors] == pytest.approx([0.5, 0.5])
    assert result.contributors[0].density_per_m2 == pytest.approx(density_small)
    assert result.contributors[1].density_per_m2 == pytest.approx(density_large)
    assert result.contributors[0].area_m2 == pytest.approx(
        _polygon_area_m2(smaller.geometry)
    )


def test_zero_population_density_uses_log_epsilon_floor() -> None:
    populated = observation(1, box(-0.001, -0.001, 0.001, 0.001))
    empty = observation(2, box(-0.002, -0.002, 0.002, 0.002), ppltn_min=0, ppltn_max=0)
    density_populated = _density(populated)
    expected = exp(
        0.5 * log(density_populated + DENSITY_SHADOW_LOG_EPSILON)
        + 0.5 * log(0.0 + DENSITY_SHADOW_LOG_EPSILON)
    )

    result = score_cafe_density_shadow(0.0, 0.0, [populated, empty], now=NOW)

    assert result.density_per_m2 == pytest.approx(expected)
    assert result.contributors is not None
    assert result.contributors[1].density_per_m2 == 0.0


def test_missing_ppltn_observations_are_excluded_and_counted() -> None:
    scored = observation(1, box(-0.001, -0.001, 0.001, 0.001))
    missing_max = observation(
        2, box(-0.002, -0.002, 0.002, 0.002), ppltn_max=None
    )
    missing_min = observation(
        3, box(-0.003, -0.003, 0.003, 0.003), ppltn_min=None
    )

    result = score_cafe_density_shadow(
        0.0, 0.0, [scored, missing_max, missing_min], now=NOW
    )

    assert result.excluded_missing_ppltn == 2
    assert result.contributors is not None
    assert [item.hotspot_id for item in result.contributors] == [1]
    assert result.density_per_m2 == pytest.approx(_density(scored))


def test_all_missing_ppltn_yields_uncovered_with_full_exclusion_count() -> None:
    first = observation(1, box(-0.001, -0.001, 0.001, 0.001), ppltn_min=None)
    second = observation(2, box(-0.002, -0.002, 0.002, 0.002), ppltn_max=None)

    result = score_cafe_density_shadow(0.0, 0.0, [first, second], now=NOW)

    assert result.coverage == "uncovered"
    assert result.selection_mode == "uncovered"
    assert result.density_per_m2 is None
    assert result.confidence is None
    assert result.contributors is None
    assert result.excluded_missing_ppltn == 2


def test_ppltn_validation_rejects_negative_and_inverted_ranges() -> None:
    area = box(-0.001, -0.001, 0.001, 0.001)
    with pytest.raises(ValueError, match="ppltn values must be non-negative"):
        score_cafe_density_shadow(
            0.0, 0.0, [observation(1, area, ppltn_min=-1, ppltn_max=10)], now=NOW
        )
    with pytest.raises(ValueError, match="ppltn_min must not exceed ppltn_max"):
        score_cafe_density_shadow(
            0.0, 0.0, [observation(1, area, ppltn_min=100, ppltn_max=10)], now=NOW
        )


def test_polygon_area_outside_supported_range_fails_closed() -> None:
    # A 2-by-2-degree polygon is far larger than any real hotspot.
    oversized = observation(1, box(-1.0, -1.0, 1.0, 1.0))
    with pytest.raises(ValueError, match="outside the supported range"):
        score_cafe_density_shadow(0.0, 0.0, [oversized], now=NOW)


def test_output_is_deterministic_and_order_independent() -> None:
    observations = [
        observation(9, box(-0.002, -0.002, 0.002, 0.002)),
        observation(4, box(-0.001, -0.001, 0.001, 0.001)),
    ]

    first = score_cafe_density_shadow(0.0, 0.0, observations, now=NOW)
    second = score_cafe_density_shadow(0.0, 0.0, observations, now=NOW)
    reverse = score_cafe_density_shadow(
        0.0, 0.0, list(reversed(observations)), now=NOW
    )

    assert first == second
    assert first == reverse


def test_duplicate_hotspot_or_area_code_fails_closed() -> None:
    area = box(-0.001, -0.001, 0.001, 0.001)
    with pytest.raises(ValueError, match="duplicate hotspot_id"):
        score_cafe_density_shadow(
            0.0, 0.0, [observation(1, area), observation(1, area)], now=NOW
        )

    clashing = DensityHotspotObservation(
        hotspot_id=2,
        area_cd="POI001",
        name="핫스팟 2",
        geometry_version="fixture-v1",
        geometry_normalization="original",
        geometry=area,
        ppltn_min=1500,
        ppltn_max=2500,
        observed_at=NOW,
    )
    with pytest.raises(ValueError, match="duplicate area_cd"):
        score_cafe_density_shadow(
            0.0, 0.0, [observation(1, area), clashing], now=NOW
        )


def test_boundary_mode_single_contributor_reports_that_density() -> None:
    # West boundary is about 111m east of the cafe; the representative interior
    # is farther, but density is taken from the single nearest polygon.
    nearby = observation(1, box(0.001, -0.001, 0.003, 0.001))

    result = score_cafe_density_shadow(
        0.0, 0.0, [nearby], now=NOW, k_neighbors=1
    )

    assert result.selection_mode == "boundary"
    assert result.coverage == "covered"
    assert result.nearest_boundary_distance_m == pytest.approx(111.2, abs=0.5)
    assert result.density_per_m2 == pytest.approx(_density(nearby))
    assert result.contributors is not None
    assert result.contributors[0].weight == pytest.approx(1.0)


def test_uncovered_never_invents_density_or_contributors() -> None:
    distant = observation(1, box(0.02, -0.001, 0.03, 0.001))

    result = score_cafe_density_shadow(
        0.0, 0.0, [distant], now=NOW, r_max_m=1_000
    )

    assert result.coverage == "uncovered"
    assert result.selection_mode == "uncovered"
    assert result.density_per_m2 is None
    assert result.confidence is None
    assert result.contributors is None
    assert result.overlap_area_codes == ()
    assert result.nearest_boundary_distance_m is not None
    assert result.nearest_boundary_distance_m > 1_000


def test_timezone_naive_inputs_fail_closed() -> None:
    area = box(-0.001, -0.001, 0.001, 0.001)
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        score_cafe_density_shadow(
            0.0, 0.0, [observation(1, area)], now=NOW.replace(tzinfo=None)
        )

    stale = observation(1, area, age_minutes=0)
    naive = DensityHotspotObservation(
        hotspot_id=stale.hotspot_id,
        area_cd=stale.area_cd,
        name=stale.name,
        geometry_version=stale.geometry_version,
        geometry_normalization=stale.geometry_normalization,
        geometry=stale.geometry,
        ppltn_min=stale.ppltn_min,
        ppltn_max=stale.ppltn_max,
        observed_at=NOW.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        score_cafe_density_shadow(0.0, 0.0, [naive], now=NOW)


def test_invalid_geometry_inputs_fail_closed() -> None:
    invalid = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    with pytest.raises(ValueError, match="normalized valid Polygon"):
        score_cafe_density_shadow(0.0, 0.0, [observation(1, invalid)], now=NOW)
