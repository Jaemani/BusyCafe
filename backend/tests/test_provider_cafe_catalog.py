from __future__ import annotations

import hashlib
import json

import pytest

from app.ingest.overture_places import OvertureCafeRecord
from app.ingest.provider_cafe_catalog import (
    ProviderCatalogError,
    build_provider_cafe_catalog,
    build_provider_catalog_manifest,
    serialize_provider_catalog,
    serialize_provider_catalog_manifest,
)
from app.ingest.seoul_refreshment_candidates import PlaceCandidate
from app.schemas import KakaoPlace


def _overture(identifier: str, **overrides: object) -> OvertureCafeRecord:
    values: dict[str, object] = {
        "overture_id": identifier,
        "name": f"오버처 {identifier}",
        "lat": 37.55,
        "lng": 126.98,
        "primary_category": "cafe",
        "confidence": 0.9,
        "road_address": None,
        "phone": None,
        "website": None,
        "sources": [],
    }
    values.update(overrides)
    return OvertureCafeRecord(**values)  # type: ignore[arg-type]


def _permit(identifier: str, **overrides: object) -> PlaceCandidate:
    values: dict[str, object] = {
        "source": "seoul_refreshment_permits",
        "source_id": identifier,
        "name": f"인허가 {identifier}",
        "latitude": 37.55,
        "longitude": 126.98,
        "category": "커피숍",
        "road_address": None,
        "lot_address": None,
        "phone": None,
    }
    values.update(overrides)
    return PlaceCandidate(**values)  # type: ignore[arg-type]


def _kakao(identifier: str, **overrides: object) -> KakaoPlace:
    values: dict[str, object] = {
        "place_id": identifier,
        "place_name": f"카카오 {identifier}",
        "category_name": "음식점 > 카페",
        "category_group_code": "CE7",
        "category_group_name": "카페",
        "phone": "",
        "address_name": "서울 종로구 테스트동 1",
        "road_address_name": "서울 종로구 테스트로 1",
        "longitude": 126.98,
        "latitude": 37.55,
        "place_url": f"http://place.map.kakao.com/{identifier}",
        "distance": "",
    }
    values.update(overrides)
    return KakaoPlace(**values)


def _inputs() -> tuple[
    tuple[OvertureCafeRecord, ...],
    tuple[PlaceCandidate, ...],
    tuple[KakaoPlace, ...],
]:
    overture = (
        _overture("ov-existing", name="기존 카페", lat=37.51),
        _overture(
            "ov-annotated",
            name="이미 연결된 다른 상호",
            lat=37.59,
            sources=[
                {
                    "dataset_id": "OA-16095",
                    "management_number": "permit-existing",
                    "provenance": "official_open_refreshment_permit",
                    "match_rule": "exact_name",
                    "distance_m": 3.0,
                }
            ],
        ),
    )
    permits = (
        _permit(
            "permit-existing",
            name="제외 카페",
            latitude=37.53,
        ),
        _permit(
            "permit-new",
            name="새-카페",
            latitude=37.54,
            road_address="서울시 인허가 원장 도로명",
            lot_address="서울시 인허가 원장 지번",
            phone="02-111-2222",
        ),
        _permit(
            "permit-ambiguous",
            name="충돌 카페",
            latitude=37.55,
        ),
        _permit(
            "permit-unmatched",
            name="미발견 카페",
            latitude=37.57,
        ),
    )
    kakao = (
        _kakao("100", place_name="기존카페", latitude=37.51),
        _kakao(
            "200",
            place_name="새 카페",
            latitude=37.54,
            road_address_name="카카오 주소를 정식 필드로 쓰면 안 됨",
            phone="021112222",
        ),
        _kakao("300", place_name="제외 카페", latitude=37.53),
        _kakao("400", place_name="충돌카페", latitude=37.55),
        _kakao("401", place_name="충돌 카페", latitude=37.5501),
        _kakao("500", place_name="카카오 전용", latitude=37.56),
    )
    return overture, permits, kakao


def test_builder_links_existing_and_emits_only_permit_owned_new_cafes() -> None:
    overture, permits, kakao = _inputs()

    build = build_provider_cafe_catalog(overture, permits, kakao)

    assert len(build.existing_provider_refs) == 1
    existing = build.existing_provider_refs[0]
    assert existing.canonical_source == "overture"
    assert existing.canonical_source_id == "ov-existing"
    assert existing.provider_place_id == "100"
    assert existing.direct_url == "https://place.map.kakao.com/100"

    assert len(build.new_cafe_candidates) == 1
    candidate = build.new_cafe_candidates[0]
    assert candidate.canonical_source == "seoul_refreshment_permits"
    assert candidate.canonical_source_id == "permit-new"
    assert candidate.name == "새-카페"
    assert candidate.road_address == "서울시 인허가 원장 도로명"
    assert candidate.lot_address == "서울시 인허가 원장 지번"
    assert candidate.phone == "02-111-2222"
    assert candidate.provider_refs[0].provider_place_id == "200"
    assert candidate.provider_refs[0].direct_url == (
        "https://place.map.kakao.com/200"
    )

    assert build.report.existing_permit_annotation_count == 1
    assert build.report.permit_excluded_as_existing_count == 1
    assert build.report.overture_naver_direct_count == 0
    assert build.report.overture_kakao_match_count == 1
    assert build.report.overture_kakao_ambiguous_count == 0
    assert build.report.overture_kakao_unmatched_count == 5
    assert build.report.permit_kakao_candidate_count == 3
    assert build.report.permit_kakao_catalog_count == 5
    assert build.report.permit_kakao_match_count == 1
    assert build.report.permit_kakao_ambiguous_count == 1
    assert build.report.permit_kakao_unmatched_count == 1
    assert build.report.new_cafe_candidate_count == 1


def test_serialization_is_order_independent_and_omits_raw_kakao_fields() -> None:
    overture, permits, kakao = _inputs()
    first = build_provider_cafe_catalog(overture, permits, kakao)
    second = build_provider_cafe_catalog(
        tuple(reversed(overture)),
        tuple(reversed(permits)),
        tuple(reversed(kakao)),
    )

    first_cache = serialize_provider_catalog(first)
    second_cache = serialize_provider_catalog(second)

    assert first == second
    assert first_cache == second_cache
    decoded = first_cache.decode("utf-8")
    assert "address_name" not in decoded
    assert "category_group_name" not in decoded
    assert "place_url" not in decoded
    records = [json.loads(line) for line in decoded.splitlines()]
    assert [record["record_type"] for record in records] == [
        "provider_ref",
        "cafe_candidate",
    ]
    assert records[1]["road_address"] == "서울시 인허가 원장 도로명"


def test_manifest_is_deterministic_and_bound_to_cache_bytes() -> None:
    build = build_provider_cafe_catalog(*_inputs())
    cache_bytes = serialize_provider_catalog(build)

    manifest = build_provider_catalog_manifest(build, cache_bytes)
    serialized = serialize_provider_catalog_manifest(build, cache_bytes)

    assert manifest["schema_version"] == "provider-cafe-catalog-v1"
    assert manifest["complete"] is True
    assert manifest["cache_sha256"] == hashlib.sha256(cache_bytes).hexdigest()
    assert manifest["record_count"] == 2
    assert json.loads(serialized) == manifest
    assert serialized == serialize_provider_catalog_manifest(build, cache_bytes)


def test_builder_rejects_duplicate_or_non_numeric_kakao_identity() -> None:
    overture, permits, kakao = _inputs()

    with pytest.raises(ProviderCatalogError, match="duplicate Kakao place ID"):
        build_provider_cafe_catalog(overture, permits, (kakao[0], kakao[0]))

    with pytest.raises(ProviderCatalogError, match="ASCII digits"):
        build_provider_cafe_catalog(overture, permits, (_kakao("not-a-number"),))


def test_builder_rejects_duplicate_permit_identity() -> None:
    overture, permits, kakao = _inputs()

    with pytest.raises(ProviderCatalogError, match="duplicate permit source ID"):
        build_provider_cafe_catalog(overture, (permits[0], permits[0]), kakao)


def test_builder_rejects_wrong_provider_categories_and_sources() -> None:
    overture, permits, kakao = _inputs()

    with pytest.raises(ProviderCatalogError, match="not CE7"):
        build_provider_cafe_catalog(
            overture,
            permits,
            (_kakao("999", category_group_code="FD6"),),
        )

    with pytest.raises(ProviderCatalogError, match="unexpected permit source"):
        build_provider_cafe_catalog(
            overture,
            (_permit("wrong", source="another_source"),),
            kakao,
        )


@pytest.mark.parametrize(
    ("website", "place_id", "direct_url"),
    [
        (
            "http://m.place.naver.com/place/123?tab=home#top",
            "123",
            "https://m.place.naver.com/place/123",
        ),
        (
            "https://m.place.naver.com/restaurant/456/",
            "456",
            "https://m.place.naver.com/restaurant/456",
        ),
        (
            "http://map.naver.com/p/entry/place/789?c=15.0,0,0,0,dh",
            "789",
            "https://map.naver.com/p/entry/place/789",
        ),
        (
            "https://m.place.naver.com/place/321/home",
            "321",
            "https://m.place.naver.com/place/321",
        ),
        (
            "https://m.map.naver.com/p/entry/place/654",
            "654",
            "https://map.naver.com/p/entry/place/654",
        ),
    ],
)
def test_builder_extracts_strict_naver_source_detail_urls(
    website: str,
    place_id: str,
    direct_url: str,
) -> None:
    record = _overture("naver-source", website=website)

    build = build_provider_cafe_catalog((record,), (), ())

    assert build.report.overture_naver_direct_count == 1
    assert build.existing_provider_refs[0].provider == "naver"
    assert build.existing_provider_refs[0].provider_place_id == place_id
    assert build.existing_provider_refs[0].direct_url == direct_url
    assert build.existing_provider_refs[0].match_rule == "source_direct_url"
    assert build.existing_provider_refs[0].match_distance_m is None


@pytest.mark.parametrize(
    "website",
    [
        "https://naver.me/short-link",
        "https://m.place.naver.com/search/카페",
        "https://new.smartplace.naver.com/bizes/place/123",
        "https://m.place.naver.com/place/not-numeric",
        "https://m.place.naver.com/place/１２３",
        "ftp://m.place.naver.com/place/123",
        "https://m.place.naver.com:443/place/123",
    ],
)
def test_builder_ignores_non_detail_or_noncanonical_naver_urls(
    website: str,
) -> None:
    record = _overture("not-direct", website=website)

    build = build_provider_cafe_catalog((record,), (), ())

    assert build.existing_provider_refs == ()
    assert build.report.overture_naver_direct_count == 0


def test_builder_rejects_one_naver_identity_claimed_by_two_overture_rows() -> None:
    records = (
        _overture("first", website="https://m.place.naver.com/place/123"),
        _overture("second", website="http://m.place.naver.com/place/123"),
    )

    with pytest.raises(ProviderCatalogError, match="duplicate naver provider place ID"):
        build_provider_cafe_catalog(records, (), ())
