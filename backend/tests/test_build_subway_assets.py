from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_subway_assets import (
    LINE_SPECS,
    SubwayAssetError,
    _json_bytes,
    build_exits,
    build_lines,
    build_stations,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PUBLIC_DATA = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_station_asset_merges_only_nearby_interchange_rows() -> None:
    asset = build_stations(load_fixture("subway_station_master_sample.json"))

    assert [feature["properties"]["station_id"] for feature in asset["features"]] == [
        "0100",
        "0300",
    ]
    assert asset["features"][0]["properties"] == {
        "station_id": "0100",
        "name": "테스트역",
        "line_ids": ["1", "2"],
        "color": "#004A85",
        "label_priority": 8,
    }
    assert asset["features"][0]["geometry"]["coordinates"] == [
        127.00005,
        37.50005,
    ]


def test_line_asset_dedupes_reverse_geometry_and_drops_invalid_runs() -> None:
    asset = build_lines(load_fixture("subway_osm_network_sample.json"))

    assert len(asset["features"]) == 1
    feature = asset["features"][0]
    assert feature["properties"] == {
        "line_id": "1",
        "line_name": "1호선",
        "color": "#004A85",
        "label_priority": 10,
    }
    assert feature["geometry"]["type"] == "MultiLineString"
    assert feature["geometry"]["coordinates"] == [[
        [127.0, 37.5],
        [127.001, 37.501],
        [127.002, 37.502],
    ]]


def test_exit_asset_uses_official_station_identity_and_dedupes() -> None:
    stations = build_stations(load_fixture("subway_station_master_sample.json"))
    asset = build_exits(load_fixture("subway_osm_exits_sample.json"), stations)

    assert len(asset["features"]) == 3
    assert [feature["properties"] for feature in asset["features"]] == [
        {
            "station_id": "0100",
            "station_name": "테스트역",
            "exit_no": "1",
            "label_priority": 1,
            "association": "official_station",
        },
        {
            "station_id": "0300",
            "station_name": "테스트역",
            "exit_no": "2",
            "label_priority": 1,
            "association": "official_station",
        },
        {
            "station_id": "osm:35",
            "station_name": None,
            "exit_no": "3",
            "label_priority": 1,
            "association": "unlinked",
        },
    ]


def test_subway_assets_are_byte_deterministic() -> None:
    station_source = load_fixture("subway_station_master_sample.json")
    first = build_stations(station_source)
    second = build_stations(station_source)

    assert _json_bytes(first) == _json_bytes(second)


@pytest.mark.parametrize("builder", [build_stations, build_lines])
def test_missing_source_shape_fails_closed(builder) -> None:
    with pytest.raises(SubwayAssetError):
        builder({})


def test_committed_subway_assets_are_canonical_and_match_contract() -> None:
    lines = load_public_asset("seoul-subway-lines.geojson")
    stations = load_public_asset("seoul-subway-stations.geojson")
    exits = load_public_asset("seoul-subway-exits.geojson")

    assert {feature["properties"]["line_id"] for feature in lines["features"]} == set(
        LINE_SPECS
    )
    station_ids = {
        feature["properties"]["station_id"] for feature in stations["features"]
    }
    assert station_ids
    assert all(
        set(feature["properties"]) == {
            "station_id",
            "name",
            "line_ids",
            "color",
            "label_priority",
        }
        for feature in stations["features"]
    )
    assert all(
        set(feature["properties"]) == {
            "station_id",
            "station_name",
            "exit_no",
            "label_priority",
            "association",
        }
        and (
            feature["properties"]["station_id"] in station_ids
            or feature["properties"]["station_id"].startswith("osm:")
        )
        for feature in exits["features"]
    )
    for asset in (lines, stations, exits):
        assert asset["features"]
        for lng, lat in coordinates(asset):
            assert 126.76 <= lng <= 127.20
            assert 37.41 <= lat <= 37.72


def load_public_asset(name: str):
    path = PUBLIC_DATA / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.read_bytes() == _json_bytes(payload)
    return payload


def coordinates(value):
    if isinstance(value, dict):
        if "coordinates" in value:
            yield from coordinates(value["coordinates"])
        else:
            for item in value.values():
                yield from coordinates(item)
    elif isinstance(value, list):
        if len(value) == 2 and all(isinstance(part, (int, float)) for part in value):
            yield value
        else:
            for item in value:
                yield from coordinates(item)
