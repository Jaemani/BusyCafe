"""Tests for scripts/seed_cafes.py's --confidence-report mode.

--confidence-report is read-only by contract (no DB writes, no network): it
only reads an already-downloaded local cache extract. These tests build that
local extract with DuckDB directly (no S3/httpfs) so no network call is ever
possible, and assert the CLI never reaches the network- or DB-touching code
paths.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from scripts.seed_cafes import main


def _write_cache(path: Path, rows: list[dict[str, object]]) -> None:
    """Write a synthetic local Overture cache extract; never touches a network."""

    connection = duckdb.connect()
    try:
        connection.execute(
            """
            CREATE TABLE cache (
              overture_id VARCHAR,
              name VARCHAR,
              lat DOUBLE,
              lng DOUBLE,
              primary_category VARCHAR,
              confidence DOUBLE,
              road_address VARCHAR,
              phone VARCHAR,
              website VARCHAR,
              sources_json VARCHAR
            )
            """
        )
        for row in rows:
            connection.execute(
                "INSERT INTO cache VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    row["overture_id"],
                    row["name"],
                    row["lat"],
                    row["lng"],
                    row["primary_category"],
                    row["confidence"],
                    row.get("road_address"),
                    row.get("phone"),
                    row.get("website"),
                    row.get("sources_json", "[]"),
                ],
            )
        connection.execute("COPY cache TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


def _row(identifier: str, confidence: float, category: str = "cafe") -> dict[str, object]:
    return {
        "overture_id": identifier,
        "name": f"카페 {identifier}",
        "lat": 37.55,
        "lng": 126.98,
        "primary_category": category,
        "confidence": confidence,
    }


def test_confidence_report_is_read_only_and_flags_pre_filtered_cache(
    tmp_path, monkeypatch, capsys
) -> None:
    cache = tmp_path / "cache.parquet"
    _write_cache(
        cache,
        [
            _row("overture:1", 0.81, "cafe"),
            _row("overture:2", 0.90, "coffee_shop"),
            _row("overture:3", 0.95, "cafe"),
        ],
    )

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("--confidence-report must not touch the network or DB")

    monkeypatch.setattr("scripts.seed_cafes.cache_seoul_extract", _fail)
    monkeypatch.setattr("scripts.seed_cafes.create_db_engine", _fail)

    exit_code = main(["--cache", str(cache), "--confidence-report"])
    assert exit_code == 0

    output = capsys.readouterr().out
    assert "records in cache: 3" in output
    assert "pass current threshold (>= 0.80): 3/3" in output
    assert "cache filtered, re-download required" in output
    assert "- [0.80, 0.85): 1" in output
    assert "- [0.90, 0.95): 1" in output
    assert "- [0.95, 1.00): 1" in output
    assert "- cafe: 2" in output
    assert "- coffee_shop: 1" in output


def test_confidence_report_honors_min_confidence_override_for_pass_count(
    tmp_path, monkeypatch, capsys
) -> None:
    cache = tmp_path / "cache.parquet"
    # 0.50 sits exactly on the report's lowest bucket edge, so the observed
    # floor equals the report range floor and nothing reads as filtered.
    _write_cache(cache, [_row("overture:1", 0.50), _row("overture:2", 0.90)])

    monkeypatch.setattr(
        "scripts.seed_cafes.create_db_engine",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB must not be touched")),
    )

    exit_code = main(
        ["--cache", str(cache), "--confidence-report", "--min-confidence", "0.50"]
    )
    assert exit_code == 0

    output = capsys.readouterr().out
    assert "pass current threshold (>= 0.50): 2/2" in output
    # Full range is present at this floor, so nothing should read as filtered.
    assert "cache filtered" not in output


def test_confidence_report_rejects_download_combo(tmp_path) -> None:
    cache = tmp_path / "missing.parquet"
    with pytest.raises(SystemExit):
        main(["--cache", str(cache), "--confidence-report", "--download"])


def test_confidence_report_rejects_apply_combo(tmp_path) -> None:
    cache = tmp_path / "missing.parquet"
    with pytest.raises(SystemExit):
        main(["--cache", str(cache), "--confidence-report", "--apply"])


def test_confidence_report_requires_existing_cache(tmp_path) -> None:
    cache = tmp_path / "missing.parquet"
    with pytest.raises(SystemExit):
        main(["--cache", str(cache), "--confidence-report"])


def test_omitting_confidence_report_flag_leaves_default_path_untouched(
    tmp_path, monkeypatch
) -> None:
    """--confidence-report defaults to False; the default seed path must be

    reached exactly as before this flag existed.
    """

    cache = tmp_path / "default-path.parquet"
    _write_cache(cache, [_row("overture:1", 0.90)])
    calls: list[str] = []
    monkeypatch.setattr(
        "scripts.seed_cafes.create_db_engine",
        lambda *a, **k: calls.append("create_db_engine") or (_ for _ in ()).throw(
            RuntimeError("stop before a real DB connection")
        ),
    )

    with pytest.raises(RuntimeError, match="stop before a real DB connection"):
        main(["--cache", str(cache)])

    assert calls == ["create_db_engine"]
