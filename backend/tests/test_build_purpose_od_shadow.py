from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.ingest.purpose_od import REQUIRED_COLUMNS
from scripts import build_purpose_od_shadow as shadow


TARGET_DATE = "2026-06-30"
WEST = "11110530"
CENTER = "11110540"
EAST = "11110550"


ROWS = (
    f"{WEST},{CENTER},0800,0900,내국인,한국,4,1000,20,30,{TARGET_DATE.replace('-', '')}",
    f"{EAST},{CENTER},0820,0920,내국인,한국,4,1200,25,10,{TARGET_DATE.replace('-', '')}",
    f"{CENTER},{WEST},0900,10,내국인,한국,1,1000,20,5,{TARGET_DATE.replace('-', '')}",
    f"{CENTER},{CENTER},0940,0940,내국인,한국,7,0,0,20,{TARGET_DATE.replace('-', '')}",
    f"{EAST},{WEST},10,11,내국인,한국,5,3000,60,7.5,{TARGET_DATE.replace('-', '')}",
)


def _csv(path: Path, rows: tuple[str, ...] = ROWS) -> Path:
    path.write_text(
        ",".join(REQUIRED_COLUMNS) + "\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def _centroids(path: Path, *, include_east: bool = True) -> Path:
    entries = [
        {
            "code": WEST,
            "name": "서쪽동",
            "kind": "seoul_admin_dong",
            "lat": 37.5,
            "lng": 126.9,
        },
        {
            "code": CENTER,
            "name": "가운데동",
            "kind": "seoul_admin_dong",
            "lat": 37.5,
            "lng": 127.0,
        },
    ]
    if include_east:
        entries.append(
            {
                "code": EAST,
                "name": "동쪽동",
                "kind": "seoul_admin_dong",
                "lat": 37.5,
                "lng": 127.1,
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema_version": "purpose-od-centroids-test-v1",
                "source": {
                    "name": "fixture-boundaries",
                    "version": "2026-06-30",
                    "commit": "abc123",
                    "sha256": "fixture-source-hash",
                },
                "centroids": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _build(
    tmp_path: Path,
    *,
    output_name: str = "shadow.json",
    centroid_path: Path | None = None,
    hours: frozenset[int] | None = frozenset({9}),
    apply: bool = False,
) -> shadow.PurposeOdArtifactResult:
    source = tmp_path / "purpose.csv"
    if not source.exists():
        _csv(source)
    if centroid_path is None:
        centroid_path = tmp_path / "centroids.json"
        if not centroid_path.exists():
            _centroids(centroid_path)
    return shadow.build_purpose_od_shadow(
        input_path=source,
        centroid_path=centroid_path,
        target_date=shadow._parse_iso_date(TARGET_DATE, field_name="test"),
        source_version="oa-22300-20260630-test",
        schema_version="oa-22300-purpose-od-csv-v1",
        output_path=tmp_path / output_name,
        hours=hours,
        apply=apply,
    )


def test_hour_shadow_uses_arrival_and_departure_semantics_with_engine(
    tmp_path: Path,
) -> None:
    result = _build(tmp_path)

    assert len(result.artifact["movements"]) == 1
    movement = result.artifact["movements"][0]
    assert movement["administrative_zone_code"] == CENTER
    assert movement["zone_name"] == "가운데동"
    assert movement["hour"] == 9
    assert movement["inbound_estimated_count"] == 60
    # Center departures at 09:00: 5 to WEST plus 20 intrazonal.
    assert movement["outbound_estimated_count"] == 25
    assert movement["net_estimated_count"] == 35
    assert movement["intrazonal_inbound_estimated_count"] == 20
    assert movement["intrazonal_outbound_estimated_count"] == 20
    assert movement["purpose_estimated_counts"] == {"4": 40, "7": 20}
    assert movement["mean_source_distance_m"] == pytest.approx(700.0)
    assert movement["mean_source_duration_min"] == pytest.approx(14.166667)
    vector = movement["movement_vector"]
    assert vector["travel_heading_deg"] == pytest.approx(90.0, abs=0.1)
    assert vector["origin_bearing_deg"] == pytest.approx(270.0, abs=0.1)
    assert vector["direction_strength"] == pytest.approx(0.5, abs=0.01)
    assert vector["eligible_estimated_count_coverage"] == 1.0

    coverage = result.artifact["coverage"]
    assert coverage["source_rows_scanned"] == 5
    assert coverage["selected_rows"] == 4
    assert coverage["centroid_code_ratio"] == 1.0
    assert result.artifact["target"]["hours"] == [9]
    assert result.artifact["time_contract"] == {
        "normalization": "floor-to-hour; source mixed 60/20-minute bins",
        "distinct_source_start_bins": 5,
        "distinct_source_finish_bins": 5,
    }


def test_all_day_includes_union_of_inbound_and_outbound_zone_hours(
    tmp_path: Path,
) -> None:
    result = _build(tmp_path, hours=None)
    keys = {
        (item["administrative_zone_code"], item["hour"])
        for item in result.artifact["movements"]
    }

    # WEST:08 is outbound-only; WEST:10 is inbound-only.
    assert (WEST, 8) in keys
    assert (WEST, 10) in keys
    outbound_only = next(
        item
        for item in result.artifact["movements"]
        if item["administrative_zone_code"] == WEST and item["hour"] == 8
    )
    assert outbound_only["inbound_estimated_count"] == 0
    assert outbound_only["outbound_estimated_count"] == 30
    assert outbound_only["mean_source_distance_m"] is None
    assert outbound_only["purpose_estimated_counts"] == {}


def test_missing_centroids_reduce_coverage_without_inventing_direction(
    tmp_path: Path,
) -> None:
    centroid_path = _centroids(tmp_path / "partial.json", include_east=False)
    result = _build(tmp_path, centroid_path=centroid_path)
    coverage = result.artifact["coverage"]

    assert coverage["observed_zone_codes"] == 3
    assert coverage["matched_centroid_zone_codes"] == 2
    assert coverage["centroid_code_ratio"] == pytest.approx(2 / 3)
    assert coverage["missing_origin_codes"] == [EAST]
    movement = result.artifact["movements"][0]
    assert movement["movement_vector"]["eligible_estimated_count_coverage"] == 0.75
    assert movement["movement_vector"]["travel_heading_deg"] == pytest.approx(
        90.0, abs=0.1
    )

    no_centroids = shadow.build_purpose_od_shadow(
        input_path=tmp_path / "purpose.csv",
        centroid_path=None,
        target_date=shadow._parse_iso_date(TARGET_DATE, field_name="test"),
        source_version="oa-22300-test",
        schema_version="schema-test",
        output_path=tmp_path / "without-centroids.json",
        hours=frozenset({9}),
    )
    vector = no_centroids.artifact["movements"][0]["movement_vector"]
    assert no_centroids.artifact["centroids"]["available"] is False
    assert no_centroids.artifact["coverage"]["centroid_code_ratio"] == 0.0
    assert vector["travel_heading_deg"] is None
    assert vector["direction_strength"] is None
    assert vector["eligible_estimated_count_coverage"] == 0.0


def test_provenance_hashes_versions_and_deterministic_bytes(tmp_path: Path) -> None:
    first = _build(tmp_path)
    second = _build(tmp_path, output_name="second.json")

    assert first.serialized == second.serialized
    assert first.artifact["source"]["sha256"] == hashlib.sha256(
        (tmp_path / "purpose.csv").read_bytes()
    ).hexdigest()
    assert first.artifact["source"]["version"] == "oa-22300-20260630-test"
    assert first.artifact["source"]["schema_version"] == (
        "oa-22300-purpose-od-csv-v1"
    )
    centroid_meta = first.artifact["centroids"]
    assert centroid_meta["schema_version"] == "purpose-od-centroids-test-v1"
    assert centroid_meta["source"]["version"] == "2026-06-30"
    assert centroid_meta["sha256"] == hashlib.sha256(
        (tmp_path / "centroids.json").read_bytes()
    ).hexdigest()
    assert first.artifact["provenance"]["network_calls"] is False
    assert first.artifact["artifact"]["public_model_effect"].startswith("none")


def test_dry_run_atomic_apply_and_overwrite_refusal(tmp_path: Path) -> None:
    dry_run = _build(tmp_path)
    output = tmp_path / "shadow.json"
    assert not output.exists()
    assert not (tmp_path / "shadow.json.part").exists()

    applied = _build(tmp_path, apply=True)
    assert output.read_bytes() == applied.serialized == dry_run.serialized
    assert not (tmp_path / "shadow.json.part").exists()

    with pytest.raises(shadow.PurposeOdArtifactError, match="overwrite"):
        _build(tmp_path)


def test_publish_failure_removes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(shadow.os, "link", fail_link)
    with pytest.raises(OSError, match="simulated publish failure"):
        _build(tmp_path, apply=True)
    assert not (tmp_path / "shadow.json").exists()
    assert not (tmp_path / "shadow.json.part").exists()


def test_cli_prints_compact_summary_and_is_dry_run_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _csv(tmp_path / "purpose.csv")
    centroids = _centroids(tmp_path / "centroids.json")
    output = tmp_path / "artifact.json"
    args = [
        "--input",
        str(source),
        "--centroids",
        str(centroids),
        "--target-date",
        TARGET_DATE,
        "--source-version",
        "source-test",
        "--schema-version",
        "schema-test",
        "--output",
        str(output),
        "--hour",
        "9",
    ]

    assert shadow.main(args) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["mode"] == "dry-run"
    assert summary["movement_groups"] == 1
    assert summary["source_rows_scanned"] == 5
    assert "movements" not in summary
    assert "dry-run" in captured.err
    assert not output.exists()

    assert shadow.main([*args, "--apply"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "apply"
    assert output.exists()


def test_mixed_date_and_swapped_coordinate_fail_closed(tmp_path: Path) -> None:
    bad_row = ROWS[0][:-8] + "20260629"
    _csv(tmp_path / "purpose.csv", (bad_row,))
    _centroids(tmp_path / "centroids.json")
    with pytest.raises(shadow.PurposeOdArtifactError, match="outside target_date"):
        _build(tmp_path)

    payload = json.loads((tmp_path / "centroids.json").read_text(encoding="utf-8"))
    payload["centroids"][0]["lat"] = 126.9
    (tmp_path / "centroids.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _csv(tmp_path / "purpose.csv")
    with pytest.raises(shadow.PurposeOdArtifactError, match="lat"):
        _build(tmp_path)
