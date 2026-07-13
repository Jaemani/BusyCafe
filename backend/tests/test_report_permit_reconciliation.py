from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.ingest.permit_reconciliation import reconcile_candidates
from app.ingest.seoul_refreshment_candidates import PlaceCandidate
from scripts.cache_refreshment_candidates import serialize_candidates
from scripts.report_permit_reconciliation import (
    build_manifest,
    main,
    publish_manifest,
    read_catalog_overture,
    read_catalog_sqlite,
)


def _candidate(
    identifier: str,
    *,
    category: str = "커피숍",
    phone: str | None = "02-1234-5678",
) -> PlaceCandidate:
    return PlaceCandidate(
        source="seoul_refreshment_permits",
        source_id=identifier,
        name=f"카페 {identifier}",
        latitude=37.55,
        longitude=126.98,
        category=category,
        road_address="민감 주소",
        lot_address=None,
        phone=phone,
    )


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE cafes (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              lat REAL NOT NULL,
              lng REAL NOT NULL,
              primary_category TEXT,
              phone TEXT,
              active INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO cafes VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "카페 match", 37.55, 126.98, "cafe", "02-1234-5678", 1),
                (2, "비활성", 37.55, 126.98, "cafe", None, 0),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_read_catalog_uses_read_only_active_view(tmp_path: Path) -> None:
    database = tmp_path / "cafes.db"
    _database(database)
    before = database.read_bytes()

    catalog = read_catalog_sqlite(database)

    assert len(catalog) == 1
    assert catalog[0].catalog_id == "1"
    assert database.read_bytes() == before


def test_manifest_is_aggregate_only_atomic_and_immutable(tmp_path: Path) -> None:
    candidate = _candidate("match")
    catalog = read_catalog_sqlite(_make_database(tmp_path))
    result = reconcile_candidates([candidate], catalog)
    manifest = build_manifest(result, candidate_cache_sha256="abc")
    output = tmp_path / "manifest.json"

    publish_manifest(output, manifest)

    content = output.read_text(encoding="utf-8")
    assert json.loads(content) == manifest
    assert "카페 match" not in content
    assert "민감 주소" not in content
    assert "02-1234-5678" not in content
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        publish_manifest(output, manifest)


def _make_database(tmp_path: Path) -> Path:
    database = tmp_path / "cafes.db"
    _database(database)
    return database


def test_cli_creates_aggregate_report_and_review_goes_stdout_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = _make_database(tmp_path)
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_bytes(
        serialize_candidates([_candidate("match"), _candidate("unmatched", phone=None)])
    )
    report = tmp_path / "report.json"

    assert main(
        [
            "--candidates",
            str(candidates),
            "--database",
            str(database),
            "--output",
            str(report),
        ]
    ) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["matched_count"] == 1
    capsys.readouterr()

    unused_report = tmp_path / "must-not-exist.json"
    assert main(
        [
            "--candidates",
            str(candidates),
            "--database",
            str(database),
            "--output",
            str(unused_report),
            "--review-unmatched",
            "1",
        ]
    ) == 0
    review = json.loads(capsys.readouterr().out)
    assert review["source_id"] == "unmatched"
    assert not unused_report.exists()


def test_overture_cache_option_loads_records_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate("match")
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_bytes(serialize_candidates([candidate]))
    report = tmp_path / "overture-report.json"

    class Record:
        overture_id = "overture:1"
        name = "카페 match"
        lat = 37.55
        lng = 126.98
        primary_category = "cafe"
        phone = "02-1234-5678"

    cache = tmp_path / "cache.parquet"
    cache.write_bytes(b"fixture-cache")
    monkeypatch.setattr(
        "scripts.report_permit_reconciliation.iter_cached_records",
        lambda path: iter((Record(),)),
    )
    monkeypatch.setattr(
        "scripts.report_permit_reconciliation.read_catalog_sqlite",
        lambda path: (_ for _ in ()).throw(AssertionError("DB must not be read")),
    )

    assert len(read_catalog_overture(cache)) == 1
    assert main(
        [
            "--candidates",
            str(candidates),
            "--overture-cache",
            str(cache),
            "--output",
            str(report),
        ]
    ) == 0
    manifest = json.loads(report.read_text(encoding="utf-8"))
    assert manifest["catalog_source"] == "overture_cache"
    assert manifest["matched_count"] == 1
    assert "catalog_cache_sha256" in manifest


def test_catalog_options_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--database",
                str(tmp_path / "db"),
                "--overture-cache",
                str(tmp_path / "cache"),
            ]
        )


def test_existing_output_preflight_skips_overture_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "exists.json"
    report.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.report_permit_reconciliation.read_candidate_cache",
        lambda path: (_ for _ in ()).throw(AssertionError("input must not be read")),
    )

    assert main(
        [
            "--overture-cache",
            str(tmp_path / "cache"),
            "--output",
            str(report),
        ]
    ) == 1
