from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest.curated_cafe_catalog import (
    build_curated_catalog,
    iter_curated_records,
    serialize_curated_records,
)
from app.ingest.overture_places import OvertureCafeRecord, OvertureIngestError
from app.ingest.seoul_refreshment_candidates import PlaceCandidate
from scripts.build_curated_cafe_catalog import build_manifest, publish_build


def _overture(identifier: str, **overrides: object) -> OvertureCafeRecord:
    values: dict[str, object] = {
        "overture_id": identifier,
        "name": f"카페 {identifier}",
        "lat": 37.55,
        "lng": 126.98,
        "primary_category": "cafe",
        "confidence": 0.5,
        "road_address": None,
        "phone": None,
        "website": None,
        "sources": [{"dataset": "overture-source"}],
    }
    values.update(overrides)
    return OvertureCafeRecord(**values)  # type: ignore[arg-type]


def _permit(identifier: str, **overrides: object) -> PlaceCandidate:
    values: dict[str, object] = {
        "source": "seoul_refreshment_permits",
        "source_id": identifier,
        "name": f"카페 {identifier}",
        "latitude": 37.55,
        "longitude": 126.98,
        "category": "커피숍",
        "road_address": None,
        "lot_address": None,
        "phone": None,
    }
    values.update(overrides)
    return PlaceCandidate(**values)  # type: ignore[arg-type]


def test_curated_gate_keeps_high_and_only_uniquely_matched_low_records() -> None:
    records = [
        _overture("high", confidence=0.8, lng=126.90),
        _overture("low-match", name="유일 카페", lng=126.95),
        _overture("low-unmatched", name="없는 카페", lng=127.00),
        _overture("amb-a", name="중복 카페", lng=127.05),
        _overture("amb-b", name="중복 카페", lng=127.05, lat=37.5501),
    ]
    permits = [
        _permit("permit-match", name="유일 카페", longitude=126.95),
        _permit("permit-amb", name="중복 카페", longitude=127.05),
    ]

    build = build_curated_catalog(records, permits)

    assert [record.overture_id for record in build.records] == ["high", "low-match"]
    assert build.report.high_confidence_count == 1
    assert build.report.incremental_low_confidence_count == 1
    assert build.report.excluded_low_confidence_count == 3
    assert build.report.curated_count == 2
    annotation = build.records[1].sources[1]
    assert annotation["dataset_id"] == "OA-16095"
    assert annotation["management_number"] == "permit-match"
    assert annotation["match_rule"] == "exact_name"


def test_build_is_deterministic_and_deduplicates_source_annotations() -> None:
    annotation = {
        "dataset_id": "OA-16095",
        "management_number": "permit",
        "provenance": "official_open_refreshment_permit",
        "match_rule": "exact_name",
        "distance_m": 0.0,
    }
    record = _overture(
        "low",
        name="일치 카페",
        sources=[annotation, {"dataset": "base"}, annotation],
    )
    permit = _permit("permit", name="일치 카페")

    first = build_curated_catalog([record], [permit])
    second = build_curated_catalog(list(reversed([record])), [permit])

    assert serialize_curated_records(first.records) == serialize_curated_records(
        second.records
    )
    assert first.records[0].sources.count(annotation) == 1


def test_duplicate_overture_identity_fails_closed() -> None:
    record = _overture("duplicate", confidence=0.9)
    with pytest.raises(OvertureIngestError, match="duplicate Overture ID"):
        build_curated_catalog([record, record], [])


def test_immutable_cache_round_trip_and_aggregate_manifest(tmp_path: Path) -> None:
    record = _overture("high", confidence=0.9, name="민감 상호")
    build = build_curated_catalog([record], [])
    output = tmp_path / "curated.jsonl"
    manifest = tmp_path / "manifest.json"

    publish_build(output, manifest, build)

    assert tuple(iter_curated_records(output)) == build.records
    aggregate = json.loads(manifest.read_text(encoding="utf-8"))
    assert aggregate == build_manifest(build, output.read_bytes())
    assert "민감 상호" not in manifest.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        publish_build(output, manifest, build)


def test_loader_rejects_unknown_contract_fields(tmp_path: Path) -> None:
    cache = tmp_path / "bad.jsonl"
    cache.write_text('{"unexpected":true}\n', encoding="utf-8")
    with pytest.raises(OvertureIngestError, match="line 1"):
        tuple(iter_curated_records(cache))
