from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from app.scoring.polygon_shadow import (
    PolygonHotspotGeometry,
    PolygonHotspotObservation,
    score_cafe_polygon_shadow,
    score_cafe_polygon_shadow_compatible,
)
from app.scoring.engine import HotspotObservation


NOW = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


def observation(
    hotspot_id: int,
    geometry,
    *,
    level: int = 1,
    age_minutes: float = 0,
    normalization: str = "original",
) -> PolygonHotspotObservation:
    return PolygonHotspotObservation(
        hotspot_id=hotspot_id,
        area_cd=f"POI{hotspot_id:03d}",
        name=f"핫스팟 {hotspot_id}",
        geometry_version="fixture-v1",
        geometry_normalization=normalization,
        geometry=geometry,
        level=level,
        observed_at=NOW - timedelta(minutes=age_minutes),
    )


def test_inside_polygon_has_priority_over_nearby_outside_polygon() -> None:
    covering = observation(2, box(-0.001, -0.001, 0.001, 0.001), level=4)
    nearby = observation(1, box(0.0011, -0.001, 0.002, 0.001), level=1)

    result = score_cafe_polygon_shadow(0.0, 0.0, [nearby, covering], now=NOW)

    assert result.selection_mode == "inside"
    assert result.coverage == "covered"
    assert result.score == pytest.approx(4.0)
    assert result.level == 4
    assert result.overlap_area_codes == ()
    assert result.contributors is not None
    assert [item.hotspot_id for item in result.contributors] == [2]
    assert result.contributors[0].inside_polygon is True
    assert result.contributors[0].boundary_distance_m == 0.0


def test_overlap_keeps_every_covering_polygon_without_arbitrary_winner() -> None:
    first = observation(7, box(-0.002, -0.002, 0.002, 0.002), level=1)
    second = observation(
        3,
        box(-0.001, -0.001, 0.003, 0.003),
        level=4,
        normalization="make_valid",
    )

    result = score_cafe_polygon_shadow(
        0.0,
        0.0,
        [first, second],
        now=NOW,
        k_neighbors=1,
    )

    assert result.selection_mode == "inside"
    assert result.score == pytest.approx(2.5)
    assert result.level == 3
    assert result.overlap_area_codes == ("POI003", "POI007")
    assert result.contributors is not None
    assert [item.hotspot_id for item in result.contributors] == [3, 7]
    assert [item.weight for item in result.contributors] == pytest.approx([0.5, 0.5])
    assert result.contributors[0].geometry_version == "fixture-v1"
    assert result.contributors[0].geometry_normalization == "make_valid"


def test_outside_uses_polygon_boundary_distance_and_nearest_k() -> None:
    # The polygon's representative point is kilometres east, but its western
    # boundary is about 111 metres from the cafe.
    elongated = observation(1, box(0.001, -0.001, 0.1, 0.001), level=2)
    farther = observation(2, box(0.004, -0.001, 0.005, 0.001), level=4)

    result = score_cafe_polygon_shadow(
        0.0,
        0.0,
        [farther, elongated],
        now=NOW,
        covered_m=200,
        k_neighbors=1,
    )

    assert result.selection_mode == "boundary"
    assert result.coverage == "covered"
    assert result.nearest_boundary_distance_m == pytest.approx(111.2, abs=0.5)
    assert result.contributors is not None
    assert [item.hotspot_id for item in result.contributors] == [1]
    assert result.score == pytest.approx(2.0)


def test_boundary_selection_corrects_longitude_scale_at_seoul_latitude() -> None:
    cafe_lat = 37.0
    cafe_lng = 127.0
    # In raw degrees the northern box looks nearer (0.001 < 0.0012). In local
    # metres the eastern box is nearer because longitude is scaled by cos(lat).
    east = box(
        cafe_lng + 0.0012,
        cafe_lat - 0.0001,
        cafe_lng + 0.0014,
        cafe_lat + 0.0001,
    )
    north = box(
        cafe_lng - 0.0001,
        cafe_lat + 0.001,
        cafe_lng + 0.0001,
        cafe_lat + 0.0012,
    )
    area = observation(1, MultiPolygon([east, north]), level=2)

    result = score_cafe_polygon_shadow(cafe_lat, cafe_lng, [area], now=NOW)

    assert result.contributors is not None
    # East boundary: about 106.6m. Raw-degree selection of north would be 111.2m.
    assert result.contributors[0].boundary_distance_m == pytest.approx(106.6, abs=0.6)
    assert result.contributors[0].boundary_distance_m < 108.0


def test_boundary_transition_keeps_score_continuous_for_same_area() -> None:
    area = observation(1, box(0.0, 0.0, 0.01, 0.01), level=3)

    on_boundary = score_cafe_polygon_shadow(0.005, 0.0, [area], now=NOW)
    just_outside = score_cafe_polygon_shadow(
        0.005,
        -0.00001,
        [area],
        now=NOW,
    )

    assert on_boundary.selection_mode == "inside"
    assert just_outside.selection_mode == "boundary"
    assert on_boundary.score == just_outside.score == 3.0
    assert on_boundary.nearest_boundary_distance_m == 0.0
    assert just_outside.nearest_boundary_distance_m == pytest.approx(1.11, abs=0.05)


def test_uncovered_never_invents_score_or_contributors() -> None:
    distant = observation(1, box(0.02, -0.001, 0.03, 0.001), level=4)

    result = score_cafe_polygon_shadow(
        0.0,
        0.0,
        [distant],
        now=NOW,
        r_max_m=1_000,
    )

    assert result.coverage == "uncovered"
    assert result.selection_mode == "uncovered"
    assert result.score is None
    assert result.level is None
    assert result.confidence is None
    assert result.confidence_tier is None
    assert result.contributors is None
    assert result.overlap_area_codes == ()
    assert result.nearest_boundary_distance_m is not None
    assert result.nearest_boundary_distance_m > 1_000


def test_input_order_does_not_change_overlap_result_or_evidence() -> None:
    observations = [
        observation(9, box(-0.002, -0.002, 0.002, 0.002), level=2),
        observation(4, box(-0.001, -0.001, 0.001, 0.001), level=4),
    ]

    forward = score_cafe_polygon_shadow(0.0, 0.0, observations, now=NOW)
    reverse = score_cafe_polygon_shadow(
        0.0,
        0.0,
        list(reversed(observations)),
        now=NOW,
    )

    assert forward == reverse


def test_invalid_or_ambiguous_geometry_inputs_fail_closed() -> None:
    invalid = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    with pytest.raises(ValueError, match="normalized valid Polygon"):
        score_cafe_polygon_shadow(
            0.0,
            0.0,
            [observation(1, invalid)],
            now=NOW,
        )

    area = box(-0.001, -0.001, 0.001, 0.001)
    with pytest.raises(ValueError, match="duplicate hotspot_id"):
        score_cafe_polygon_shadow(
            0.0,
            0.0,
            [observation(1, area), observation(1, area)],
            now=NOW,
        )

    with pytest.raises(ValueError, match="now must be timezone-aware"):
        score_cafe_polygon_shadow(
            0.0,
            0.0,
            [],
            now=NOW.replace(tzinfo=None),
        )


def test_common_evaluator_adapter_preserves_prediction_and_fails_on_missing_geometry() -> None:
    geometry = PolygonHotspotGeometry(
        area_cd="POI001",
        name="핫스팟 1",
        geometry_version="fixture-v1",
        geometry_normalization="original",
        geometry=box(-0.001, -0.001, 0.001, 0.001),
    )
    observation = HotspotObservation(
        hotspot_id=1,
        name="핫스팟 1",
        lat=0.0,
        lng=0.0,
        level=3,
        observed_at=NOW,
    )

    estimate = score_cafe_polygon_shadow_compatible(
        0.0,
        0.0,
        [observation],
        {1: geometry},
        now=NOW,
    )

    assert estimate.coverage == "covered"
    assert estimate.level == 3
    assert estimate.primary_hotspot_id == 1
    assert estimate.primary_distance_m == 0.0
    assert estimate.contributors is not None
    assert estimate.contributors[0].weight == pytest.approx(1.0)

    with pytest.raises(ValueError, match="missing polygon geometry"):
        score_cafe_polygon_shadow_compatible(
            0.0,
            0.0,
            [observation],
            {},
            now=NOW,
        )
