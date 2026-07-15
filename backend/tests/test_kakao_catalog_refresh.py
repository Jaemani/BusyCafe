from __future__ import annotations

import pytest

from app.ingest.kakao_catalog_refresh import (
    KakaoRefreshTarget,
    build_kakao_display_refresh,
)
from app.schemas import KakaoPlace


def _place(identifier: str, **overrides: object) -> KakaoPlace:
    values: dict[str, object] = {
        "id": identifier,
        "place_name": f"카페 {identifier}",
        "category_name": "음식점 > 카페",
        "category_group_code": "CE7",
        "category_group_name": "카페",
        "phone": "02-1234-5678",
        "address_name": f"서울 종로구 테스트동 {identifier}",
        "road_address_name": f"서울 종로구 테스트로 {identifier}",
        "x": 126.98,
        "y": 37.55,
        "place_url": f"http://place.map.kakao.com/{identifier}",
        "distance": "",
    }
    values.update(overrides)
    return KakaoPlace.model_validate(values)


def _target(identifier: int, **overrides: object) -> KakaoRefreshTarget:
    values: dict[str, object] = {
        "cafe_id": identifier,
        "origin_provider": "kakao",
        "kakao_place_id": str(identifier),
        "match_method": "source_primary",
        "name": f"이전 카페 {identifier}",
        "latitude": 37.55,
        "longitude": 126.98,
        "road_address": f"서울 종로구 이전로 {identifier}",
        "phone": None,
        "source_release": "previous",
    }
    values.update(overrides)
    return KakaoRefreshTarget(**values)  # type: ignore[arg-type]


def test_refresh_uses_x_as_longitude_y_as_latitude_and_reports_moves() -> None:
    build = build_kakao_display_refresh(
        (
            _place("1", x=126.981, y=37.551),
            _place("2", x=127.10, y=37.65),
        ),
        (_target(1), _target(2)),
        source_release="current",
        large_move_m=250.0,
        move_buckets_m=(10.0, 50.0, 250.0, 1_000.0),
    )

    first = build.refreshes[0]
    assert first.longitude == 126.981
    assert first.latitude == 37.551
    assert first.changed_fields == (
        "name",
        "latitude",
        "longitude",
        "road_address",
        "phone",
        "source_release",
    )
    assert build.report.eligible_target_count == 2
    assert build.report.seen_target_count == 2
    assert build.report.changed_target_count == 2
    assert build.report.coordinate_changed_count == 2
    assert build.report.large_move_count == 1
    assert sum(build.report.move_bucket_counts.values()) == 2


def test_refresh_rejects_non_seoul_address_and_outside_bbox() -> None:
    build = build_kakao_display_refresh(
        (
            _place(
                "1",
                address_name="경기 구리시 테스트동 1",
                road_address_name="경기 구리시 테스트로 1",
            ),
            _place("2", x=37.55, y=37.55),
        ),
        (_target(1), _target(2), _target(3)),
        source_release="current",
    )

    assert build.refreshes == ()
    assert build.seen_cafe_ids == ()
    assert build.report.missing_target_count == 3
    assert build.report.rejected_target_count == 2


def test_refresh_accepts_exact_provider_match_but_rejects_unsafe_match() -> None:
    exact = _target(
        1,
        origin_provider="overture",
        match_method="exact_name_and_address",
    )
    build = build_kakao_display_refresh(
        (_place("1"),), (exact,), source_release="current"
    )
    assert build.refreshes[0].origin_provider == "overture"

    for unsafe_method in ("exact_name", "exact_phone", "fuzzy_name"):
        with pytest.raises(ValueError, match="unsafe Kakao refresh match method"):
            build_kakao_display_refresh(
                (_place("2"),),
                (_target(2, match_method=unsafe_method),),
                source_release="current",
            )

    with pytest.raises(ValueError, match="source_primary.*Kakao-origin"):
        build_kakao_display_refresh(
            (_place("3"),),
            (_target(3, origin_provider="overture"),),
            source_release="current",
        )

    with pytest.raises(ValueError, match="Kakao-origin.*source_primary"):
        build_kakao_display_refresh(
            (_place("4"),),
            (_target(4, match_method="exact_name_and_address"),),
            source_release="current",
        )


def test_refresh_fails_closed_on_duplicate_provider_identity() -> None:
    with pytest.raises(ValueError, match="duplicate Kakao place ID"):
        build_kakao_display_refresh(
            (_place("1"), _place("1")),
            (_target(1),),
            source_release="current",
        )

    with pytest.raises(ValueError, match="duplicate Kakao refresh ownership"):
        build_kakao_display_refresh(
            (_place("1"),),
            (_target(1), _target(2, kakao_place_id="1")),
            source_release="current",
        )
