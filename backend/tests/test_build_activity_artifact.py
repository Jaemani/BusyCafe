from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from app.config import (
    LIVING_POPULATION_COMPACT_MANIFEST_SUFFIX,
    LIVING_POPULATION_COMPACT_QUERY_VERSION,
    LIVING_POPULATION_COMPACT_SCHEMA_VERSION,
)
from app.ingest.national_grid import cell_wgs84_corners
from scripts import build_activity_artifact as artifact
from scripts import compact_living_population as compact


TARGET = date(2026, 7, 13)  # Monday
HISTORY = ("2026-06-22", "2026-06-29", "2026-07-06")
CELL_A = "다사52505325"
CELL_B = "다사52755375"
CELL_C = "다사53005500"
CELL_D = "다사53255525"


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
    rows: list[tuple[str, int, str, Decimal, int, int, str]],
    *,
    schema_version: int = LIVING_POPULATION_COMPACT_SCHEMA_VERSION,
    query_version: str = LIVING_POPULATION_COMPACT_QUERY_VERSION,
) -> Path:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE source(
                date DATE,
                hour UTINYINT,
                cell_id VARCHAR,
                known_total DECIMAL(38, 5),
                fragment_count UINTEGER,
                masked_fragment_count UINTEGER,
                fragments_json VARCHAR
            )
            """
        )
        connection.executemany("INSERT INTO source VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        connection.execute("COPY source TO ? (FORMAT PARQUET)", [str(path)])
        schema = [
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
            for row in connection.execute("DESCRIBE source").fetchall()
        ]
    finally:
        connection.close()
    parquet_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": schema_version,
        "query_version": query_version,
        "mode": "apply",
        "row_counts": {"cell_observations_filtered": len(rows)},
        "output": {
            "path": str(path.resolve()),
            "schema": schema,
            "size_bytes": path.stat().st_size,
            "sha256": parquet_sha,
        },
    }
    path.with_name(path.name + LIVING_POPULATION_COMPACT_MANIFEST_SUFFIX).write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _manifest_path(path: Path) -> Path:
    return path.with_name(path.name + LIVING_POPULATION_COMPACT_MANIFEST_SUFFIX)


def _read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(_manifest_path(path).read_text(encoding="utf-8"))


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    _manifest_path(path).write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _known_fragment(
    value: str | int | float,
    *,
    admin_code: str = "11110515",
    source_file: str = "202606.csv",
    total_raw: str | None = None,
) -> dict[str, Any]:
    return {
        "administrative_dong_code": admin_code,
        "known_value": str(value),
        "total_raw": str(value) if total_raw is None else total_raw,
        "masked": False,
        "source_file": source_file,
    }


def _masked_fragment(
    *,
    admin_code: str = "11110515",
    source_file: str = "202606.csv",
) -> dict[str, Any]:
    return {
        "administrative_dong_code": admin_code,
        "known_value": None,
        "total_raw": "*",
        "masked": True,
        "source_file": source_file,
    }


def _canonical_fragments(fragments: list[dict[str, Any]]) -> str:
    return json.dumps(fragments, ensure_ascii=False, separators=(",", ":"))


def _row(
    day: str,
    cell_id: str,
    fragments: list[dict[str, Any]],
    *,
    hour: int = 14,
    known_total: Decimal | None = None,
    fragment_count: int | None = None,
    masked_fragment_count: int | None = None,
    fragments_json: str | None = None,
) -> tuple[str, int, str, Decimal, int, int, str]:
    return (
        day,
        hour,
        cell_id,
        known_total
        if known_total is not None
        else sum(
            (
                Decimal(fragment["known_value"])
                for fragment in fragments
                if fragment["known_value"] is not None
            ),
            Decimal(0),
        ),
        len(fragments) if fragment_count is None else fragment_count,
        sum(bool(fragment["masked"]) for fragment in fragments)
        if masked_fragment_count is None
        else masked_fragment_count,
        _canonical_fragments(fragments) if fragments_json is None else fragments_json,
    )


def _history(
    cell_id: str, total: float = 10.0
) -> list[tuple[str, int, str, Decimal, int, int, str]]:
    return [
        _row(
            day,
            cell_id,
            [_known_fragment(total, source_file="202606-07.csv")],
        )
        for day in HISTORY
    ]


def _build(
    tmp_path: Path,
    rows: list[tuple[str, int, str, Decimal, int, int, str]],
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
        _row(
            TARGET.isoformat(),
            CELL_B,
            [_known_fragment(30.0, source_file="202607.csv")],
        ),
        *_history(CELL_B, 20.0),
        _row(
            TARGET.isoformat(),
            CELL_A,
            [_known_fragment(9_999.0, source_file="202607.csv")],
        ),
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
    evidence = first.artifact["source"]["inputs"][0]
    manifest_path = tmp_path / (
        "compact.parquet" + LIVING_POPULATION_COMPACT_MANIFEST_SUFFIX
    )
    assert (
        evidence["manifest"]["sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert evidence["manifest"]["verified_output"] == {
        "file": "compact.parquet",
        "sha256": hashlib.sha256(
            (tmp_path / "compact.parquet").read_bytes()
        ).hexdigest(),
        "size_bytes": (tmp_path / "compact.parquet").stat().st_size,
        "rows": len(rows),
    }


def test_polygon_uses_exact_cell_corners_and_closed_ring(tmp_path: Path) -> None:
    result = _build(
        tmp_path,
        [
            *_history(CELL_A),
            _row(
                TARGET.isoformat(),
                CELL_A,
                [_known_fragment(12.0, source_file="now.csv")],
            ),
        ],
    )

    ring = result.artifact["features"][0]["geometry"]["coordinates"][0]
    expected = [[lng, lat] for lat, lng in cell_wgs84_corners(CELL_A)]
    assert ring == [*expected, expected[0]]
    assert ring[0] == ring[-1]


def test_missing_baseline_and_missing_current_fail_soft(tmp_path: Path) -> None:
    rows = [
        # Current exists, but no historical normal: never invent a baseline.
        _row(
            TARGET.isoformat(),
            CELL_A,
            [_known_fragment(50.0, source_file="target.csv")],
        ),
        # Historical normal exists, but no target row: baseline-only.
        *_history(CELL_B, 25.0),
    ]
    features = _features_by_id(_build(tmp_path, rows))

    unsupported = features[CELL_A]["properties"]
    assert unsupported["target_status"] == "complete"
    assert unsupported["source_observation"]["known_total"] == "50.00000"
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
            _row(
                TARGET.isoformat(),
                CELL_A,
                [_masked_fragment(source_file="target.csv")],
            ),
        ],
    )
    properties = result.artifact["features"][0]["properties"]

    assert properties["target_status"] == "masked"
    assert properties["observed_at"] == "2026-07-13T14:00:00+09:00"
    assert properties["source_observation"] == {
        "known_total": "0.00000",
        "fragment_count": 1,
        "masked_fragment_count": 1,
        "fragments": [
            {
                "administrative_dong_code": "11110515",
                "known_value": None,
                "total_raw": "*",
                "masked": True,
                "source_file": "target.csv",
            }
        ],
        "fragments_json": _canonical_fragments(
            [_masked_fragment(source_file="target.csv")]
        ),
    }
    assert properties["activity"]["signal_mode"] == "baseline_only"
    assert properties["activity"]["current_value"] is None
    assert properties["activity"]["current_value_min"] is None
    assert properties["activity"]["current_value_max"] is None
    assert properties["activity"]["anomaly_log1p"] is None
    assert result.artifact["counts"]["masked_target"] == 1


def test_partially_masked_target_abstains_and_preserves_fragments(
    tmp_path: Path,
) -> None:
    fragments = [
        _known_fragment("7.25000", admin_code="11110515", source_file="target.csv"),
        _masked_fragment(admin_code="11110530", source_file="target.csv"),
    ]
    result = _build(
        tmp_path,
        [*_history(CELL_A, 18.0), _row(TARGET.isoformat(), CELL_A, fragments)],
    )
    properties = result.artifact["features"][0]["properties"]

    assert properties["target_status"] == "partially_masked"
    assert properties["source_observation"] == {
        "known_total": "7.25000",
        "fragment_count": 2,
        "masked_fragment_count": 1,
        "fragments": fragments,
        "fragments_json": _canonical_fragments(fragments),
    }
    activity = properties["activity"]
    assert activity["signal_mode"] == "baseline_only"
    assert activity["current_value"] is None
    assert activity["current_value_min"] is None
    assert activity["current_value_max"] is None
    assert activity["anomaly_log1p"] is None
    assert activity["anomaly_log1p_min"] is None
    assert activity["anomaly_log1p_max"] is None
    assert result.artifact["counts"]["partially_masked_target"] == 1
    assert result.artifact["counts"]["masked_target"] == 0


def test_partial_and_full_masked_history_remain_masked_observations(
    tmp_path: Path,
) -> None:
    history = [
        _row(HISTORY[0], CELL_A, [_known_fragment("30")]),
        _row(
            HISTORY[1],
            CELL_A,
            [
                _known_fragment("8", admin_code="11110515"),
                _masked_fragment(admin_code="11110530"),
            ],
        ),
        _row(HISTORY[2], CELL_A, [_masked_fragment()]),
    ]
    result = _build(
        tmp_path,
        [
            *history,
            _row(TARGET.isoformat(), CELL_A, [_known_fragment("40")]),
        ],
    )
    baseline = result.artifact["features"][0]["properties"]["baseline"]

    assert baseline["raw_n"] == 3
    assert baseline["masked_share"] == pytest.approx(2 / 3)


def test_counts_distinguish_all_target_mask_states(tmp_path: Path) -> None:
    rows = [
        *_history(CELL_A),
        _row(TARGET.isoformat(), CELL_A, [_known_fragment("12")]),
        *_history(CELL_B),
        _row(
            TARGET.isoformat(),
            CELL_B,
            [
                _known_fragment("4", admin_code="11110515"),
                _masked_fragment(admin_code="11110530"),
            ],
        ),
        *_history(CELL_C),
        _row(TARGET.isoformat(), CELL_C, [_masked_fragment()]),
        *_history(CELL_D),
    ]
    counts = _build(tmp_path, rows).artifact["counts"]

    assert counts["complete_target"] == 1
    assert counts["partially_masked_target"] == 1
    assert counts["masked_target"] == 1
    assert counts["missing_target"] == 1


def test_real_compactor_v2_output_is_consumed_without_contract_translation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "living.csv"
    source.write_text(
        "\n".join(
            [
                "일자,시간,행정동코드,250M격자,생활인구합계",
                *(f"{day.replace('-', '')},14,11110515,{CELL_A},18" for day in HISTORY),
                f"20260713,14,11110515,{CELL_A},7.25",
                f"20260713,14,11110530,{CELL_A},*",
            ]
        )
        + "\n",
        encoding="cp949",
    )
    allowlist = tmp_path / "cells.txt"
    allowlist.write_text(CELL_A + "\n", encoding="utf-8")
    parquet = tmp_path / "producer.parquet"
    compact.compact_living_population(
        inputs=[source],
        cell_ids_path=allowlist,
        output_path=parquet,
        apply=True,
    )

    result = artifact.build_activity_artifact(
        inputs=[parquet],
        calendar_path=_calendar(tmp_path / "calendar.json"),
        target_date=TARGET,
        hour=14,
        source_version="oa-22784-integration-v2",
        output_path=tmp_path / "activity.geojson",
    )

    properties = result.artifact["features"][0]["properties"]
    assert properties["target_status"] == "partially_masked"
    assert properties["source_observation"]["known_total"] == "7.25000"
    assert properties["activity"]["signal_mode"] == "baseline_only"


def test_dry_run_and_atomic_apply_refuse_overwrite(tmp_path: Path) -> None:
    rows = [
        *_history(CELL_A),
        _row(
            TARGET.isoformat(),
            CELL_A,
            [_known_fragment(12.0, source_file="target.csv")],
        ),
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
        _row(
            TARGET.isoformat(),
            CELL_A,
            [_known_fragment(12.0, source_file="target.csv")],
        ),
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
            [
                _row(
                    "2026-07-14",
                    CELL_C,
                    [_known_fragment(10.0, source_file="future.csv")],
                )
            ],
        )


def test_missing_compact_manifest_fails_closed(tmp_path: Path) -> None:
    source = _parquet(tmp_path / "compact.parquet", _history(CELL_A))
    _manifest_path(source).unlink()

    with pytest.raises(artifact.ActivityArtifactError, match="manifest does not exist"):
        artifact.build_activity_artifact(
            inputs=[source],
            calendar_path=_calendar(tmp_path / "calendar.json"),
            target_date=TARGET,
            hour=14,
            source_version="source-v1",
            output_path=tmp_path / "out.geojson",
        )


def test_v1_and_mixed_query_manifests_fail_closed(tmp_path: Path) -> None:
    legacy = _parquet(
        tmp_path / "legacy.parquet",
        _history(CELL_A),
        schema_version=1,
    )
    with pytest.raises(artifact.ActivityArtifactError, match="schema_version"):
        artifact.build_activity_artifact(
            inputs=[legacy],
            calendar_path=_calendar(tmp_path / "calendar.json"),
            target_date=TARGET,
            hour=14,
            source_version="source-v1",
            output_path=tmp_path / "legacy.geojson",
        )

    first = _parquet(tmp_path / "first.parquet", _history(CELL_A))
    second = _parquet(
        tmp_path / "second.parquet",
        _history(CELL_B),
        query_version="unexpected-query-version",
    )
    with pytest.raises(artifact.ActivityArtifactError, match="query_version"):
        artifact.build_activity_artifact(
            inputs=[first, second],
            calendar_path=tmp_path / "calendar.json",
            target_date=TARGET,
            hour=14,
            source_version="source-v1",
            output_path=tmp_path / "mixed.geojson",
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("filename", "filename"),
        ("size", "size_bytes"),
        ("sha256", "sha256"),
        ("rows", "row count"),
    ],
)
def test_manifest_output_evidence_must_match_parquet(
    tmp_path: Path, field: str, message: str
) -> None:
    source = _parquet(tmp_path / "compact.parquet", _history(CELL_A))
    manifest = _read_manifest(source)
    if field == "filename":
        manifest["output"]["path"] = str(tmp_path / "other.parquet")
    elif field == "size":
        manifest["output"]["size_bytes"] += 1
    elif field == "sha256":
        manifest["output"]["sha256"] = "0" * 64
    else:
        manifest["row_counts"]["cell_observations_filtered"] += 1
    _write_manifest(source, manifest)

    with pytest.raises(artifact.ActivityArtifactError, match=message):
        artifact.build_activity_artifact(
            inputs=[source],
            calendar_path=_calendar(tmp_path / "calendar.json"),
            target_date=TARGET,
            hour=14,
            source_version="source-v1",
            output_path=tmp_path / "out.geojson",
        )


def test_extra_parquet_column_is_rejected_even_with_valid_manifest_hash(
    tmp_path: Path,
) -> None:
    source = _parquet(tmp_path / "compact.parquet", _history(CELL_A))
    replacement = tmp_path / "replacement.parquet"
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE replacement_source AS SELECT * FROM read_parquet(?)",
            [str(source)],
        )
        connection.execute("ALTER TABLE replacement_source ADD COLUMN extra INTEGER")
        connection.execute("UPDATE replacement_source SET extra = 1")
        connection.execute(
            "COPY replacement_source TO ? (FORMAT PARQUET)", [str(replacement)]
        )
    finally:
        connection.close()
    source.unlink()
    replacement.replace(source)
    manifest = _read_manifest(source)
    manifest["output"]["size_bytes"] = source.stat().st_size
    manifest["output"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    _write_manifest(source, manifest)

    with pytest.raises(artifact.ActivityArtifactError, match="exact v2 columns"):
        artifact.build_activity_artifact(
            inputs=[source],
            calendar_path=_calendar(tmp_path / "calendar.json"),
            target_date=TARGET,
            hour=14,
            source_version="source-v1",
            output_path=tmp_path / "out.geojson",
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("zero_fragment_count", "fragment_count must be positive"),
        ("masked_count_range", "within fragment_count"),
        ("fragment_length", "length must equal"),
        ("actual_mask_count", "actual masked fragment count"),
        ("masked_known", "masked fragment must"),
        ("masked_raw", "masked fragment must"),
        ("unmasked_known", "known_value must be an exact"),
        ("unmasked_raw", "total_raw has invalid"),
        ("known_total_sum", "known_total must equal"),
        ("negative_known_total", "known_total must be finite"),
        ("admin_code", "8 ASCII digits"),
        ("source_file", "source_file must be non-empty"),
        ("fragment_fields", "exact v2 fields"),
        ("noncanonical_json", "canonical compact JSON"),
        ("invalid_json", "valid JSON"),
        ("non_array_json", "contain an array"),
        ("nonfinite_json", "finite JSON values"),
    ],
)
def test_fragment_and_count_invariants_fail_closed(
    tmp_path: Path, case: str, message: str
) -> None:
    fragment = _known_fragment("1")
    row = _row(TARGET.isoformat(), CELL_A, [fragment])
    if case == "zero_fragment_count":
        row = _row(TARGET.isoformat(), CELL_A, [fragment], fragment_count=0)
    elif case == "masked_count_range":
        row = _row(TARGET.isoformat(), CELL_A, [fragment], masked_fragment_count=2)
    elif case == "fragment_length":
        row = _row(TARGET.isoformat(), CELL_A, [fragment], fragment_count=2)
    elif case == "actual_mask_count":
        row = _row(
            TARGET.isoformat(),
            CELL_A,
            [_masked_fragment()],
            masked_fragment_count=0,
        )
    elif case == "masked_known":
        bad = {**_masked_fragment(), "known_value": "1"}
        row = _row(TARGET.isoformat(), CELL_A, [bad], known_total=Decimal(0))
    elif case == "masked_raw":
        bad = {**_masked_fragment(), "total_raw": "0"}
        row = _row(TARGET.isoformat(), CELL_A, [bad])
    elif case == "unmasked_known":
        bad = {**fragment, "known_value": "-1"}
        row = _row(TARGET.isoformat(), CELL_A, [bad], known_total=Decimal(0))
    elif case == "unmasked_raw":
        row = _row(
            TARGET.isoformat(),
            CELL_A,
            [_known_fragment("1", total_raw="1e0")],
        )
    elif case == "known_total_sum":
        row = _row(TARGET.isoformat(), CELL_A, [fragment], known_total=Decimal("2"))
    elif case == "negative_known_total":
        row = _row(TARGET.isoformat(), CELL_A, [fragment], known_total=Decimal("-1"))
    elif case == "admin_code":
        row = _row(
            TARGET.isoformat(),
            CELL_A,
            [_known_fragment("1", admin_code="１２３４５６７８")],
        )
    elif case == "source_file":
        row = _row(TARGET.isoformat(), CELL_A, [_known_fragment("1", source_file="")])
    elif case == "fragment_fields":
        bad = {**fragment, "extra": "not-allowed"}
        row = _row(TARGET.isoformat(), CELL_A, [bad])
    elif case == "noncanonical_json":
        row = _row(
            TARGET.isoformat(),
            CELL_A,
            [fragment],
            fragments_json=json.dumps([fragment]),
        )
    elif case == "invalid_json":
        row = _row(TARGET.isoformat(), CELL_A, [fragment], fragments_json="not-json")
    elif case == "non_array_json":
        row = _row(TARGET.isoformat(), CELL_A, [fragment], fragments_json="{}")
    elif case == "nonfinite_json":
        row = _row(
            TARGET.isoformat(),
            CELL_A,
            [fragment],
            fragments_json='[{"administrative_dong_code":"11110515",'
            '"known_value":NaN,"total_raw":"1","masked":false,'
            '"source_file":"202606.csv"}]',
        )

    with pytest.raises(artifact.ActivityArtifactError, match=message):
        _build(tmp_path, [row])


def test_duplicate_date_hour_cell_across_inputs_is_rejected(tmp_path: Path) -> None:
    row = _row(TARGET.isoformat(), CELL_A, [_known_fragment("1")])
    first = _parquet(tmp_path / "first.parquet", [row])
    second = _parquet(tmp_path / "second.parquet", [row])

    with pytest.raises(
        artifact.ActivityArtifactError, match="duplicate date-hour-cell"
    ):
        artifact.build_activity_artifact(
            inputs=[first, second],
            calendar_path=_calendar(tmp_path / "calendar.json"),
            target_date=TARGET,
            hour=14,
            source_version="source-v1",
            output_path=tmp_path / "out.geojson",
        )
