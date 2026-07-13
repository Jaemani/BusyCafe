from __future__ import annotations

import hashlib
import json

import pytest

from app.ingest.provider_cafe_catalog import (
    ProviderCatalogError,
    PROVIDER_CATALOG_SCHEMA_VERSION,
    read_complete_provider_catalog,
    read_provider_catalog,
)


def _reference(source: str, source_id: str, place_id: str) -> dict[str, object]:
    return {
        "canonical_source": source,
        "canonical_source_id": source_id,
        "direct_url": f"https://place.map.kakao.com/{place_id}",
        "match_distance_m": 3.0,
        "match_rule": "exact_name",
        "provider": "kakao",
        "provider_place_id": place_id,
    }


def _write_complete_catalog(tmp_path):
    records = [
        {
            "record_type": "provider_ref",
            **_reference("overture", "ov-1", "100"),
        },
        {
            "record_type": "cafe_candidate",
            "canonical_source": "seoul_refreshment_permits",
            "canonical_source_id": "permit-1",
            "name": "새 카페",
            "latitude": 37.55,
            "longitude": 126.98,
            "category": "커피숍",
            "road_address": None,
            "lot_address": "서울 테스트동 1",
            "phone": None,
            "provider_refs": [
                _reference("seoul_refreshment_permits", "permit-1", "200")
            ],
        },
    ]
    cache = tmp_path / "provider.jsonl"
    cache_bytes = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    ).encode("utf-8")
    cache.write_bytes(cache_bytes)
    manifest = cache.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": PROVIDER_CATALOG_SCHEMA_VERSION,
                "complete": True,
                "cache_sha256": hashlib.sha256(cache_bytes).hexdigest(),
                "cache_size_bytes": len(cache_bytes),
                "record_count": 2,
                "report": {
                    "overture_naver_direct_count": 0,
                    "overture_kakao_match_count": 1,
                    "new_cafe_candidate_count": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    return cache, manifest


def test_reader_round_trips_strict_provider_records(tmp_path) -> None:
    records = [
        {"record_type": "provider_ref", **_reference("overture", "ov-1", "100")},
        {
            "record_type": "cafe_candidate",
            "canonical_source": "seoul_refreshment_permits",
            "canonical_source_id": "permit-1",
            "name": "새 카페",
            "latitude": 37.55,
            "longitude": 126.98,
            "category": "커피숍",
            "road_address": None,
            "lot_address": "서울 테스트동 1",
            "phone": None,
            "provider_refs": [
                _reference("seoul_refreshment_permits", "permit-1", "200")
            ],
        },
    ]
    path = tmp_path / "provider.jsonl"
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )

    catalog = read_provider_catalog(path)

    assert len(catalog.existing_provider_refs) == 1
    assert len(catalog.new_cafe_candidates) == 1
    assert catalog.new_cafe_candidates[0].lot_address == "서울 테스트동 1"


@pytest.mark.parametrize(
    "mutation",
    [
        {"direct_url": "https://place.map.kakao.com/999"},
        {"match_distance_m": -1},
        {"provider": "unknown"},
    ],
)
def test_reader_rejects_tampered_provider_references(tmp_path, mutation) -> None:
    record = {
        "record_type": "provider_ref",
        **_reference("overture", "ov-1", "100"),
        **mutation,
    }
    path = tmp_path / "provider.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ProviderCatalogError, match="invalid provider catalog line 1"):
        read_provider_catalog(path)


def test_reader_rejects_one_provider_identity_with_two_owners(tmp_path) -> None:
    records = [
        {"record_type": "provider_ref", **_reference("overture", "ov-1", "100")},
        {"record_type": "provider_ref", **_reference("overture", "ov-2", "100")},
    ]
    path = tmp_path / "provider.jsonl"
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )

    with pytest.raises(ProviderCatalogError, match="multiple owners"):
        read_provider_catalog(path)


def test_complete_reader_binds_catalog_to_manifest(tmp_path) -> None:
    cache, manifest = _write_complete_catalog(tmp_path)

    catalog = read_complete_provider_catalog(cache, manifest)

    assert len(catalog.existing_provider_refs) == 1
    assert len(catalog.new_cafe_candidates) == 1


def test_complete_reader_requires_manifest(tmp_path) -> None:
    cache, manifest = _write_complete_catalog(tmp_path)
    manifest.unlink()

    with pytest.raises(ProviderCatalogError, match="invalid or missing"):
        read_complete_provider_catalog(cache, manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("complete", False, "incomplete"),
        ("schema_version", "wrong", "schema_version"),
        ("record_count", 1, "record_count"),
        ("cache_size_bytes", 1, "size"),
    ],
)
def test_complete_reader_rejects_invalid_manifest_contract(
    tmp_path, field, value, message
) -> None:
    cache, manifest = _write_complete_catalog(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[field] = value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProviderCatalogError, match=message):
        read_complete_provider_catalog(cache, manifest)


def test_complete_reader_rejects_same_size_catalog_tampering(tmp_path) -> None:
    cache, manifest = _write_complete_catalog(tmp_path)
    cache.write_bytes(cache.read_bytes().replace(b'"100"', b'"101"'))

    with pytest.raises(ProviderCatalogError, match="sha256"):
        read_complete_provider_catalog(cache, manifest)


def test_complete_reader_rejects_report_count_mismatch(tmp_path) -> None:
    cache, manifest = _write_complete_catalog(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["report"]["overture_kakao_match_count"] = 0
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProviderCatalogError, match="manifest report"):
        read_complete_provider_catalog(cache, manifest)
