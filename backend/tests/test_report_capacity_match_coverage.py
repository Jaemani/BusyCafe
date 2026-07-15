from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import (
    SEOUL_REFRESHMENT_PERMIT_DATASET_ID,
    SEOUL_REFRESHMENT_PERMIT_SERVICE,
)
from app.geo import haversine_m
from app.ingest.provider_cafe_catalog import PERMIT_SOURCE
from app.ingest.seoul_refreshment_candidates import PlaceCandidate
from scripts.cache_refreshment_candidates import serialize_candidates
from scripts.report_capacity_match_coverage import (
    AREA_UNIT,
    AREA_UNIT_STATUS,
    CapacityCafe,
    CapacityCoverageError,
    build_capacity_coverage,
    enforce_transaction_read_only,
    load_versioned_candidate_cache,
    publish_report,
    serialize_report,
)


ROOT = Path(__file__).resolve().parents[2]
BASE_LAT = 37.5665
BASE_LNG = 126.9780


def _permit(
    identifier: str,
    *,
    name: str,
    address: str,
    area: str,
    phone: str | None = None,
    latitude: float = BASE_LAT,
    longitude: float = BASE_LNG,
    category: str = "커피숍",
    status: str = "eligible",
    unit: str = AREA_UNIT,
    unit_status: str = AREA_UNIT_STATUS,
) -> PlaceCandidate:
    return PlaceCandidate(
        source=PERMIT_SOURCE,
        source_id=identifier,
        name=name,
        latitude=latitude,
        longitude=longitude,
        category=category,
        road_address=address,
        lot_address=None,
        phone=phone,
        facility_area_raw=area,
        facility_area_m2=area,
        facility_area_unit=unit,
        facility_area_unit_status=unit_status,
        facility_area_unit_provenance="fixture provenance",
        facility_area_status=status,  # type: ignore[arg-type]
    )


def _cafe(
    identifier: str,
    *,
    name: str,
    address: str,
    phone: str | None = None,
    latitude: float = BASE_LAT,
    longitude: float = BASE_LNG,
    provider: str = "overture",
) -> CapacityCafe:
    return CapacityCafe(
        cafe_id=identifier,
        name=name,
        latitude=latitude,
        longitude=longitude,
        road_address=address,
        phone=phone,
        origin_provider=provider,
    )


def test_aggregate_resolution_rules_percentiles_providers_and_filters() -> None:
    candidates = [
        _permit("p-name", name="Name", address="A", area="1"),
        _permit(
            "p-phone", name="Different", address="B", area="5", phone="0212345678"
        ),
        _permit(
            "p-both", name="Both", address="C", area="50", phone="0298765432"
        ),
        _permit("p-missing", name="Missing", address="D", area="95"),
        _permit("p-ambiguous", name="Twin", address="E", area="99"),
        _permit(
            "excluded-category",
            name="Name",
            address="A",
            area="500",
            category="다방",
        ),
        _permit(
            "excluded-unit",
            name="Name",
            address="A",
            area="600",
            unit="unknown",
        ),
        _permit(
            "excluded-status",
            name="Name",
            address="A",
            area="0",
            status="nonpositive",
        ),
    ]
    cafes = [
        _cafe("c-name", name="Name", address="A", provider="kakao"),
        _cafe(
            "c-phone",
            name="Other",
            address="B",
            phone="02-1234-5678",
            provider="overture",
        ),
        _cafe(
            "c-both",
            name="Both",
            address="C",
            phone="02-9876-5432",
            provider="kakao",
        ),
        _cafe("c-twin-1", name="Twin", address="E"),
        _cafe("c-twin-2", name="Twin", address="E"),
    ]

    report = build_capacity_coverage(candidates, cafes)

    assert report["scope"]["eligible_coffee_permit_count"] == 5
    assert report["resolution_counts"] == {
        "verified": 3,
        "missing": 1,
        "ambiguous": 1,
    }
    assert report["verified_evidence_rule_counts"] == {
        "name_only": 1,
        "phone_only": 1,
        "both": 1,
    }
    assert report["matched_area_m2"] == {
        "samples": 3,
        "unit": "m2",
        "percentile_method": "nearest_rank",
        "min": "1",
        "p1": "1",
        "p5": "1",
        "p50": "5",
        "p95": "50",
        "p99": "50",
        "max": "50",
    }
    assert report["matched_cafe_origin_provider_counts"] == {
        "kakao": 2,
        "overture": 1,
    }


def test_grid_prefilter_keeps_match_across_cell_boundary() -> None:
    longitude = BASE_LNG + 0.00056
    assert 0 < haversine_m(BASE_LAT, BASE_LNG, BASE_LAT, longitude) < 50
    permit = _permit(
        "boundary-permit",
        name="Boundary",
        address="Boundary address",
        area="42.90",
        longitude=BASE_LNG,
    )
    cafe = _cafe(
        "boundary-cafe",
        name="Boundary",
        address="Boundary address",
        longitude=longitude,
    )

    report = build_capacity_coverage([permit], [cafe])

    assert report["resolution_counts"]["verified"] == 1


def test_same_source_catalog_rows_are_excluded_from_independent_matching() -> None:
    permit = _permit(
        "permit-source-row",
        name="Same source cafe",
        address="Same source address",
        area="42.9",
    )
    same_source = _cafe(
        "same-source-cafe",
        name="Same source cafe",
        address="Same source address",
        provider=PERMIT_SOURCE,
    )

    report = build_capacity_coverage([permit], [same_source])

    assert report["resolution_counts"] == {
        "verified": 0,
        "missing": 1,
        "ambiguous": 0,
    }
    assert report["scope"]["active_cafe_count"] == 1
    assert report["scope"]["independent_active_cafe_count"] == 0
    assert report["scope"]["same_source_cafe_excluded_count"] == 1
    assert report["scope"]["independent_source_required"] is True


def test_same_source_duplicate_cannot_make_independent_match_ambiguous() -> None:
    permit = _permit(
        "permit-with-independent-match",
        name="Independent cafe",
        address="Independent address",
        area="42.9",
    )
    cafes = [
        _cafe(
            "same-source-copy",
            name="Independent cafe",
            address="Independent address",
            provider=PERMIT_SOURCE,
        ),
        _cafe(
            "kakao-independent",
            name="Independent cafe",
            address="Independent address",
            provider="kakao",
        ),
    ]

    report = build_capacity_coverage([permit], cafes)

    assert report["resolution_counts"]["verified"] == 1
    assert report["matched_cafe_origin_provider_counts"] == {"kakao": 1}


def test_report_is_input_order_independent_and_contains_no_source_identity() -> None:
    permits = [
        _permit("secret-permit-a", name="Secret Cafe A", address="Secret A", area="10"),
        _permit("secret-permit-b", name="Secret Cafe B", address="Secret B", area="20"),
    ]
    cafes = [
        _cafe("secret-cafe-a", name="Secret Cafe A", address="Secret A"),
        _cafe("secret-cafe-b", name="Secret Cafe B", address="Secret B"),
    ]

    left = serialize_report(build_capacity_coverage(permits, cafes))
    right = serialize_report(build_capacity_coverage(list(reversed(permits)), list(reversed(cafes))))

    assert left == right
    text = left.decode("utf-8")
    for secret in (
        "secret-permit-a",
        "secret-cafe-a",
        "Secret Cafe A",
        "Secret A",
    ):
        assert secret not in text


def test_cache_manifest_sha_and_count_are_verified(tmp_path: Path) -> None:
    candidate = _permit("cache-permit", name="Cache", address="Cache A", area="10")
    cache = tmp_path / "candidates.jsonl"
    manifest = tmp_path / "candidates.manifest.json"
    cache_bytes = serialize_candidates([candidate])
    cache.write_bytes(cache_bytes)

    def write_manifest(*, sha: str, count: int = 1) -> None:
        manifest.write_text(
            json.dumps(
                {
                    "dataset_id": SEOUL_REFRESHMENT_PERMIT_DATASET_ID,
                    "service": SEOUL_REFRESHMENT_PERMIT_SERVICE,
                    "cache_sha256": sha,
                    "candidate_count": count,
                }
            ),
            encoding="utf-8",
        )

    write_manifest(sha=hashlib.sha256(cache_bytes).hexdigest())
    loaded, provenance = load_versioned_candidate_cache(cache, manifest)
    assert loaded == (candidate,)
    assert provenance["contract_version"]

    write_manifest(sha="0" * 64)
    with pytest.raises(CapacityCoverageError, match="SHA-256"):
        load_versioned_candidate_cache(cache, manifest)

    write_manifest(sha=hashlib.sha256(cache_bytes).hexdigest(), count=2)
    with pytest.raises(CapacityCoverageError, match="count"):
        load_versioned_candidate_cache(cache, manifest)


def test_postgresql_transaction_is_explicitly_read_only() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement):
            self.statements.append(str(statement))

    session = FakeSession()
    enforce_transaction_read_only(session)
    assert session.statements == ["SET TRANSACTION READ ONLY"]


def test_report_output_is_atomic_and_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "aggregate.json"
    serialized = b'{"aggregate":true}\n'

    publish_report(output, serialized)

    assert output.read_bytes() == serialized
    assert not output.with_name(output.name + ".part").exists()
    with pytest.raises(FileExistsError, match="overwrite"):
        publish_report(output, serialized)


def test_production_workflow_is_ephemeral_read_only_and_aggregate_only() -> None:
    workflow = (
        ROOT / ".github/workflows/report-capacity-coverage-production.yml"
    ).read_text(encoding="utf-8")
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "environment: Production" in workflow
    assert "SEOUL_API_KEY: ${{ secrets.SEOUL_API_KEY }}" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "RUNNER_TEMP" in workflow
    assert "cache_refreshment_candidates.py" in workflow
    assert "report_capacity_match_coverage.py" in workflow
    assert "actions/upload-artifact@v4" in workflow
    upload = workflow[workflow.index("- name: Upload aggregate report only") :]
    assert "capacity-match-coverage.json" in upload
    assert "capacity-candidates.jsonl" not in upload
    lowered = workflow.lower()
    for forbidden in ("alembic", "seed_", "materialize", "insert into", "update cafes"):
        assert forbidden not in lowered
