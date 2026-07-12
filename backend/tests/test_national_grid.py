"""Tests for the national-grid CELL_ID decoder.

Expected coordinates come from the 2026-07-12 sample validation recorded in
``docs/research/2026-07-12-cell-id-decode.md``: 817 real cells from the
Se250MSpopLocalResd feed all decoded inside the Seoul bbox with consistent
district clustering, so the fixture cell below serves as a regression anchor.
"""

from __future__ import annotations

import pytest

from app.config import SEOUL_BBOX
from app.geo import haversine_m
from app.ingest.national_grid import DecodedCell, decode_cell_id

# First cell of backend/fixtures/se250m_spop_local_resd_sample.xml
# (H_DNG_CD 11110515, Jongno-gu).
FIXTURE_CELL = "다사52505325"


def test_decodes_fixture_cell_to_validated_position() -> None:
    decoded = decode_cell_id(FIXTURE_CELL)
    # 다=2 easting squares, 사=6 northing squares, digits in 10 m units.
    assert decoded.easting_m == 700_000 + 2 * 100_000 + 5250 * 10
    assert decoded.northing_m == 1_300_000 + 6 * 100_000 + 5325 * 10
    assert decoded.center_lat == pytest.approx(37.578539, abs=1e-4)
    assert decoded.center_lng == pytest.approx(126.963463, abs=1e-4)


def test_bounds_enclose_center_and_span_a_250m_cell() -> None:
    decoded = decode_cell_id(FIXTURE_CELL)
    assert decoded.min_lat < decoded.center_lat < decoded.max_lat
    assert decoded.min_lng < decoded.center_lng < decoded.max_lng
    height_m = haversine_m(
        decoded.min_lat, decoded.center_lng, decoded.max_lat, decoded.center_lng
    )
    width_m = haversine_m(
        decoded.center_lat, decoded.min_lng, decoded.center_lat, decoded.max_lng
    )
    assert height_m == pytest.approx(250.0, abs=5.0)
    assert width_m == pytest.approx(250.0, abs=5.0)


def test_adjacent_cells_are_one_cell_apart() -> None:
    base = decode_cell_id("다사52505325")
    north = decode_cell_id("다사52505350")
    east = decode_cell_id("다사52755325")
    assert haversine_m(
        base.center_lat, base.center_lng, north.center_lat, north.center_lng
    ) == pytest.approx(250.0, abs=5.0)
    assert haversine_m(
        base.center_lat, base.center_lng, east.center_lat, east.center_lng
    ) == pytest.approx(250.0, abs=5.0)


def test_fixture_sample_cells_decode_inside_seoul_bbox() -> None:
    # All five committed sample rows share this validated behaviour.
    for cell_id in (
        "다사52505325",
        "다사52755400",
        "다사52755425",
        "다사52755450",
        "다사52755375",
    ):
        decoded = decode_cell_id(cell_id)
        min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
        assert min_lng <= decoded.center_lng <= max_lng
        assert min_lat <= decoded.center_lat <= max_lat


def test_strips_surrounding_whitespace() -> None:
    assert decode_cell_id(f"  {FIXTURE_CELL} ") == decode_cell_id(FIXTURE_CELL)


def test_is_deterministic() -> None:
    first = decode_cell_id(FIXTURE_CELL)
    second = decode_cell_id(FIXTURE_CELL)
    assert isinstance(first, DecodedCell)
    assert first == second


@pytest.mark.parametrize(
    "cell_id",
    [
        "다사5250532",  # too short
        "다사525053250",  # too long
        "하사52505325",  # easting letter outside table
        "다자52505325",  # northing letter outside table
        "다사5250532a",  # non-digit
        "다사５２５０5325",  # full-width digits are not ASCII digits
        "다사52505326",  # off the 250m lattice
        "다사52515325",  # off-lattice easting
        "",
    ],
)
def test_rejects_invalid_cell_ids(cell_id: str) -> None:
    with pytest.raises(ValueError):
        decode_cell_id(cell_id)


def test_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        decode_cell_id(5250532)  # type: ignore[arg-type]
