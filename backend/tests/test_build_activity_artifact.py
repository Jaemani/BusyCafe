from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from app.ingest.national_grid import cell_wgs84_corners
from scripts import build_activity_artifact as artifact


TARGET = date(2026, 7, 13)  # Monday
HISTORY = ("2026-06-22", "2026-06-29", "2026-07-06")
CELL_A = "다사52505325"
CELL_B = "다사52755375"
CELL_C = "다사53005500"


def _calendar(path: Path, *, holidays: list[str] | None = None) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": "kr-calendar-test-v1",
                "timezone": "Asia/Seoul",
                "public_holidays": holidays or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _parquet(
    path: Path,
    rows: list[tuple[str, int, str, float | None, bool, str]],
) -> Path:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE source(
                date DATE,
                hour UTINYINT,
                cell_id VARCHAR,
                total DOUBLE,
                masked BOOLEAN,
                source_file VARCHAR
            )
            """
        )
        connection.executemany("INSERT INTO source VALUES (?, ?, ?, ?, ?, ?)", rows)
        connection.execute("COPY source TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()
    return path


def _history(
    cell_id: str, total: float = 10.0
) -> list[tuple[str, int, str, float | None, bool, str]]:
    return [(day, 14, cell_id, total, False, "202606-07.csv") for day in HISTORY]


def _build(
    tmp_path: Path,
    rows: list[tuple[str, int, str, float | None, bool, str]],
    *,
    output_name: str = "activity.geojson",
    apply: bool = False,
) -> artifact.ActivityArtifactResult:
    return artifact.build_activity_artifact(
        inputs=[_parquet(tmp_path / "compact.parquet", rows)],
        calendar_path=_calendar(tmp_path / "calendar.json"),
        target_date=TARGET,
        hour=14,
        source_version="oa-22784-test-v1",
        output_path=tmp_path / output_name,
        apply=apply,
    )


def _features_by_id(result: artifact.ActivityArtifactResult) -> dict[str, dict]:
    return {feature["id"]: feature for feature in result.artifact["features"]}


def test_deterministic_order_and_strict_no_leak_baseline(tmp_path: Path) -> None:
    rows = [
        (TARGET.isoformat(), 14, CELL_B, 30.0, False, "202607.csv"),
        *_history(CELL_B, 20.0),
        (TARGET.isoformat(), 14, CELL_A, 9_999.0, False, "202607.csv"),
        *_history(CELL_A, 10.0),
    ]
    first = _build(tmp_path, rows)
    second = artifact.build_activity_artifact(
        inputs=[tmp_path / "compact.parquet"],
        calendar_path=tmp_path / "calendar.json",
        target_date=TARGET,
        hour=14,
        source_version="oa-22784-test-v1",
        output_path=tmp_path / "second.geojson",
    )

    assert first.serialized == second.serialized
    assert [item["id"] for item in first.artifact["features"]] == [CELL_A, CELL_B]
    feature = _features_by_id(first)[CELL_A]
    assert feature["properties"]["baseline"]["mean"] == pytest.approx(10.0)
    assert feature["properties"]["baseline"]["raw_n"] == 3
    assert feature["properties"]["activity"]["current_value"] == 9_999.0
    assert first.artifact["provenance"]["baseline_cutoff"] == (
        "strictly-before-target-date"
    )


def test_polygon_uses_exact_cell_corners_and_closed_ring(tmp_path: Path) -> None:
    result = _build(
        tmp_path,
        [*_history(CELL_A), (TARGET.isoformat(), 14, CELL_A, 12.0, False, "now.csv")],
    )

    ring = result.artifact["features"][0]["geometry"]["coordinates"][0]
    expected = [[lng, lat] for lat, lng in cell_wgs84_corners(CELL_A)]
    assert ring == [*expected, expected[0]]
    assert ring[0] == ring[-1]


def test_missing_baseline_and_missing_current_fail_soft(tmp_path: Path) -> None:
    rows = [
        # Current exists, but no historical normal: never invent a baseline.
        (TARGET.isoformat(), 14, CELL_A, 50.0, False, "target.csv"),
        # Historical normal exists, but no target row: baseline-only.
        *_history(CELL_B, 25.0),
    ]
    features = _features_by_id(_build(tmp_path, rows))

    unsupported = features[CELL_A]["properties"]
    assert unsupported["target_status"] == "observed"
    assert unsupported["source_observation"]["total"] == 50.0
    assert unsupported["baseline"]["mean"] is None
    assert unsupported["activity"]["signal_mode"] == "unsupported"
    assert unsupported["activity"]["current_value"] is None

    baseline_only = features[CELL_B]["properties"]
    assert baseline_only["target_status"] == "missing"
    assert baseline_only["target_at"] == "2026-07-13T14:00:00+09:00"
    assert baseline_only["observed_at"] is None
    assert baseline_only["baseline"]["mean"] == pytest.approx(25.0)
    assert baseline_only["activity"]["signal_mode"] == "baseline_only"
    assert baseline_only["activity"]["freshness"] == "n/a"
    assert baseline_only["activity"]["current_value"] is None


def test_masked_target_is_not_imputed_as_current_activity(tmp_path: Path) -> None:
    result = _build(
        tmp_path,
        [
            *_history(CELL_A, 18.0),
            (TARGET.isoformat(), 14, CELL_A, None, True, "target.csv"),
        ],
    )
    properties = result.artifact["features"][0]["properties"]

    assert properties["target_status"] == "masked"
    assert properties["observed_at"] == "2026-07-13T14:00:00+09:00"
    assert properties["source_observation"] == {
        "total": None,
        "masked": True,
        "source_file": "target.csv",
    }
    assert properties["activity"]["signal_mode"] == "baseline_only"
    assert properties["activity"]["current_value"] is None
    assert properties["activity"]["anomaly_log1p"] is None
    assert result.artifact["counts"]["masked_target"] == 1


def test_dry_run_and_atomic_apply_refuse_overwrite(tmp_path: Path) -> None:
    rows = [
        *_history(CELL_A),
        (TARGET.isoformat(), 14, CELL_A, 12.0, False, "target.csv"),
    ]
    dry_run = _build(tmp_path, rows)
    output = tmp_path / "activity.geojson"
    assert not output.exists()
    assert not (tmp_path / "activity.geojson.part").exists()

    applied = artifact.build_activity_artifact(
        inputs=[tmp_path / "compact.parquet"],
        calendar_path=tmp_path / "calendar.json",
        target_date=TARGET,
        hour=14,
        source_version="oa-22784-test-v1",
        output_path=output,
        apply=True,
    )
    assert output.read_bytes() == applied.serialized == dry_run.serialized
    assert not (tmp_path / "activity.geojson.part").exists()

    with pytest.raises(artifact.ActivityArtifactError, match="overwrite"):
        artifact.build_activity_artifact(
            inputs=[tmp_path / "compact.parquet"],
            calendar_path=tmp_path / "calendar.json",
            target_date=TARGET,
            hour=14,
            source_version="oa-22784-test-v1",
            output_path=output,
            apply=True,
        )


def test_publish_failure_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        *_history(CELL_A),
        (TARGET.isoformat(), 14, CELL_A, 12.0, False, "target.csv"),
    ]
    source = _parquet(tmp_path / "compact.parquet", rows)
    calendar = _calendar(tmp_path / "calendar.json")
    output = tmp_path / "activity.geojson"

    def fail_link(source_path: Path, destination_path: Path) -> None:
        raise OSError(f"simulated publish failure: {source_path} -> {destination_path}")

    monkeypatch.setattr(artifact.os, "link", fail_link)
    with pytest.raises(OSError, match="simulated publish failure"):
        artifact.build_activity_artifact(
            inputs=[source],
            calendar_path=calendar,
            target_date=TARGET,
            hour=14,
            source_version="oa-22784-test-v1",
            output_path=output,
            apply=True,
        )

    assert not output.exists()
    assert not (tmp_path / "activity.geojson.part").exists()


def test_calendar_must_be_explicit_and_versioned(tmp_path: Path) -> None:
    source = _parquet(tmp_path / "compact.parquet", _history(CELL_A))
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        json.dumps({"version": "v1", "public_holidays": []}), encoding="utf-8"
    )

    with pytest.raises(artifact.ActivityArtifactError, match="timezone"):
        artifact.build_activity_artifact(
            inputs=[source],
            calendar_path=calendar,
            target_date=TARGET,
            hour=14,
            source_version="source-v1",
            output_path=tmp_path / "out.geojson",
        )


def test_no_rows_at_or_before_target_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(artifact.ActivityArtifactError, match="no rows"):
        _build(
            tmp_path,
            [("2026-07-14", 14, CELL_C, 10.0, False, "future.csv")],
        )
