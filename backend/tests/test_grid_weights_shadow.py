from __future__ import annotations

import pytest
from shapely.affinity import scale
from shapely.geometry import Polygon, box

from app.ingest.hotspot_master import HotspotGeometryRecord
from app.ingest.national_grid import (
    CELL_GEOMETRY_VERSION,
    CELL_SIZE_M,
    cell_wgs84_corners,
)
from app.scoring.grid_weights_shadow import (
    generate_hotspot_cell_weights_shadow,
)


CELL_A = "다사52505325"
CELL_B = "다사52755325"


def _cell_polygon(cell_id: str) -> Polygon:
    return Polygon((lng, lat) for lat, lng in cell_wgs84_corners(cell_id))


def _hotspot(
    area_cd: str, geometry: Polygon, *, version: str = "fixture-hotspot-v1"
) -> HotspotGeometryRecord:
    return HotspotGeometryRecord(
        area_cd=area_cd,
        name=f"hotspot-{area_cd}",
        category="fixture",
        geometry_version=version,
        normalization="original",
        geometry=geometry,
    )


def test_full_cell_intersection_has_unit_weight_and_provenance() -> None:
    result = generate_hotspot_cell_weights_shadow(
        [_hotspot("POI001", _cell_polygon(CELL_A))],
        [CELL_A],
    )

    assert len(result) == 1
    weight = result[0]
    assert weight.area_cd == "POI001"
    assert weight.hotspot_geometry_version == "fixture-hotspot-v1"
    assert weight.cell_id == CELL_A
    assert weight.cell_geometry_version == CELL_GEOMETRY_VERSION
    assert "shadow-unverified" in weight.cell_geometry_version
    assert weight.cell_fraction == pytest.approx(1.0)
    assert weight.intersection_area_m2 == pytest.approx(CELL_SIZE_M**2)


def test_partial_cell_uses_intersection_over_whole_cell_area() -> None:
    cell = _cell_polygon(CELL_A)
    half_cell = scale(cell, xfact=0.5, yfact=1.0, origin="centroid")

    result = generate_hotspot_cell_weights_shadow(
        [_hotspot("POI001", half_cell)],
        [CELL_A],
    )

    expected_fraction = half_cell.intersection(cell).area / cell.area
    assert result[0].cell_fraction == pytest.approx(expected_fraction)
    assert result[0].intersection_area_m2 == pytest.approx(
        CELL_SIZE_M**2 * expected_fraction
    )


def test_boundary_only_touch_is_excluded() -> None:
    cell = _cell_polygon(CELL_A)
    min_lng, min_lat, max_lng, max_lat = cell.bounds
    touching = box(max_lng, min_lat, max_lng + 0.001, max_lat)

    assert generate_hotspot_cell_weights_shadow(
        [_hotspot("POI001", touching)],
        [CELL_A],
    ) == ()


def test_output_is_deterministic_and_sorted() -> None:
    combined = _cell_polygon(CELL_A).union(_cell_polygon(CELL_B)).convex_hull
    hotspots = [
        _hotspot("POI002", combined),
        _hotspot("POI001", combined),
    ]

    forward = generate_hotspot_cell_weights_shadow(
        hotspots,
        [CELL_B, CELL_A],
    )
    reverse = generate_hotspot_cell_weights_shadow(
        list(reversed(hotspots)),
        [CELL_A, CELL_B],
    )

    assert forward == reverse
    assert [(item.area_cd, item.cell_id) for item in forward] == [
        ("POI001", CELL_A),
        ("POI001", CELL_B),
        ("POI002", CELL_A),
        ("POI002", CELL_B),
    ]


def test_empty_side_produces_no_weights() -> None:
    assert generate_hotspot_cell_weights_shadow([], [CELL_A]) == ()
    assert generate_hotspot_cell_weights_shadow(
        [_hotspot("POI001", _cell_polygon(CELL_A))], []
    ) == ()


def test_duplicate_normalized_cell_id_fails_closed() -> None:
    with pytest.raises(ValueError, match="duplicate cell_id"):
        generate_hotspot_cell_weights_shadow(
            [_hotspot("POI001", _cell_polygon(CELL_A))],
            [CELL_A, f" {CELL_A} "],
        )


def test_duplicate_hotspot_code_fails_closed() -> None:
    geometry = _cell_polygon(CELL_A)
    with pytest.raises(ValueError, match="duplicate hotspot area_cd"):
        generate_hotspot_cell_weights_shadow(
            [_hotspot("POI001", geometry), _hotspot("POI001", geometry)],
            [CELL_A],
        )


def test_invalid_hotspot_geometry_fails_closed() -> None:
    invalid = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    with pytest.raises(ValueError, match="normalized valid polygon"):
        generate_hotspot_cell_weights_shadow(
            [_hotspot("POI001", invalid)],
            [CELL_A],
        )
