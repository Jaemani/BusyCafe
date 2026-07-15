from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_purpose_od_centroids as centroids


def _feature(
    *,
    adm_cd2: str,
    sido: str,
    sgg: str,
    adm_nm: str,
    sidonm: str,
    sggnm: str,
    min_lng: float,
    min_lat: float,
    size: float = 0.01,
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "adm_cd2": adm_cd2,
            "sido": sido,
            "sgg": sgg,
            "adm_nm": adm_nm,
            "sidonm": sidonm,
            "sggnm": sggnm,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [min_lng, min_lat],
                    [min_lng + size, min_lat],
                    [min_lng + size, min_lat + size],
                    [min_lng, min_lat + size],
                    [min_lng, min_lat],
                ]
            ],
        },
    }


def _source(path: Path, *, reverse: bool = False) -> Path:
    features = [
        _feature(
            adm_cd2="1111053000",
            sido="11",
            sgg="11110",
            adm_nm="서울특별시 종로구 사직동",
            sidonm="서울특별시",
            sggnm="종로구",
            min_lng=126.97,
            min_lat=37.57,
        ),
        _feature(
            adm_cd2="4111151000",
            sido="41",
            sgg="41111",
            adm_nm="경기도 수원시 장안구 파장동",
            sidonm="경기도",
            sggnm="수원시 장안구",
            min_lng=127.00,
            min_lat=37.30,
        ),
        _feature(
            adm_cd2="4111152000",
            sido="41",
            sgg="41111",
            adm_nm="경기도 수원시 장안구 율천동",
            sidonm="경기도",
            sggnm="수원시 장안구",
            min_lng=127.02,
            min_lat=37.30,
        ),
    ]
    if reverse:
        features.reverse()
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return path


def _build(tmp_path: Path, *, source_name: str = "source.geojson", apply=False):
    return centroids.build_purpose_od_centroids(
        source_geojson=_source(tmp_path / source_name),
        source_version="ver-test",
        source_commit="abc123",
        output_path=tmp_path / "centroids.json",
        apply=apply,
    )


def test_builds_seoul_dong_and_dissolved_sgg_centroids(tmp_path: Path) -> None:
    result = _build(tmp_path)
    rows = {item["code"]: item for item in result.artifact["centroids"]}

    assert result.artifact["counts"] == {
        "seoul_admin_dong": 1,
        "si_gun_gu": 2,
        "total": 3,
    }
    assert rows["11110530"]["kind"] == "seoul_admin_dong"
    assert rows["11110000"]["kind"] == "si_gun_gu"
    assert rows["41111000"]["kind"] == "si_gun_gu"
    assert rows["41111000"]["lng"] == pytest.approx(127.015, abs=0.001)
    assert rows["41111000"]["lat"] == pytest.approx(37.305, abs=0.001)


def test_output_order_is_input_order_independent(tmp_path: Path) -> None:
    first = _build(tmp_path)
    second_source = _source(tmp_path / "reverse.geojson", reverse=True)
    second = centroids.build_purpose_od_centroids(
        source_geojson=second_source,
        source_version="ver-test",
        source_commit="abc123",
        output_path=tmp_path / "second.json",
    )

    # Source hashes differ because feature order differs; the actual centroid
    # contract remains stable and sorted.
    assert first.artifact["centroids"] == second.artifact["centroids"]
    assert [item["code"] for item in first.artifact["centroids"]] == sorted(
        item["code"] for item in first.artifact["centroids"]
    )


def test_dry_run_apply_and_overwrite_refusal(tmp_path: Path) -> None:
    dry = _build(tmp_path)
    assert not dry.output_path.exists()

    applied = centroids.build_purpose_od_centroids(
        source_geojson=tmp_path / "source.geojson",
        source_version="ver-test",
        source_commit="abc123",
        output_path=tmp_path / "centroids.json",
        apply=True,
    )
    assert applied.output_path.read_bytes() == applied.serialized
    assert not (tmp_path / "centroids.json.part").exists()
    with pytest.raises(centroids.PurposeOdCentroidError, match="overwrite"):
        centroids.build_purpose_od_centroids(
            source_geojson=tmp_path / "source.geojson",
            source_version="ver-test",
            source_commit="abc123",
            output_path=tmp_path / "centroids.json",
        )


def test_rejects_malformed_admin_code(tmp_path: Path) -> None:
    source = _source(tmp_path / "source.geojson")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["features"][0]["properties"]["adm_cd2"] = "not-a-code"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(centroids.PurposeOdCentroidError, match="adm_cd2"):
        centroids.build_purpose_od_centroids(
            source_geojson=source,
            source_version="ver-test",
            source_commit="abc123",
            output_path=tmp_path / "centroids.json",
        )
