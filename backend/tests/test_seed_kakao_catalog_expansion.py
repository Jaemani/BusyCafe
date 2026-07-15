from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import PROVIDER_VERIFIED_CAFE_CONFIDENCE
from app.models import Base, Cafe, CafeProviderPlace
from app.schemas import KakaoPlace
from scripts.seed_kakao_catalog_expansion import (
    KAKAO_LARGE_MOVE_QUARANTINE_REASON,
    KAKAO_SOURCE_MATCH_METHOD,
    KakaoCatalogApplyError,
    ValidatedKakaoSnapshot,
    seed_kakao_catalog_expansion,
)


GENERATED_AT = datetime(2026, 7, 14, 12, 34, 56, tzinfo=UTC)


def _place(identifier: str, *, lng: float, lat: float) -> KakaoPlace:
    return KakaoPlace.model_validate(
        {
            "id": identifier,
            "place_name": f"카페 {identifier}",
            "category_name": "음식점 > 카페",
            "category_group_code": "CE7",
            "category_group_name": "카페",
            "phone": "02-1234-5678",
            "address_name": f"서울 종로구 테스트동 {identifier}",
            "road_address_name": f"서울 종로구 테스트로 {identifier}",
            "x": lng,
            "y": lat,
            "place_url": f"http://place.map.kakao.com/{identifier}",
            "distance": "",
        }
    )


def _snapshot(*places: KakaoPlace) -> ValidatedKakaoSnapshot:
    return ValidatedKakaoSnapshot(
        places=places,
        generated_at=GENERATED_AT,
        source_release=GENERATED_AT.isoformat(),
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def _add_kakao_cafe(
    session: Session,
    identifier: str,
    *,
    lat: float,
    lng: float,
) -> tuple[Cafe, CafeProviderPlace]:
    cafe = Cafe(
        origin_provider="kakao",
        origin_source_id=identifier,
        source_release="previous",
        source_confidence=1.0,
        primary_category="cafe",
        name=f"이전 {identifier}",
        lat=lat,
        lng=lng,
        road_address=f"서울 종로구 이전로 {identifier}",
        phone=None,
        active=True,
    )
    session.add(cafe)
    session.flush()
    link = CafeProviderPlace(
        cafe_id=cafe.id,
        provider="kakao",
        provider_place_id=identifier,
        detail_url=f"https://place.map.kakao.com/{identifier}",
        active=True,
        match_method=KAKAO_SOURCE_MATCH_METHOD,
        match_distance_m=0.0,
        verified_at=datetime(2026, 7, 13, tzinfo=UTC),
        last_seen_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    session.add(link)
    return cafe, link


def test_dry_run_writes_nothing_then_apply_is_atomic_and_idempotent(
    session: Session,
) -> None:
    snapshot = _snapshot(
        _place("100", lng=126.90, lat=37.42),
        _place("200", lng=127.10, lat=37.70),
    )

    dry = seed_kakao_catalog_expansion(
        session,
        snapshot,
        apply=False,
        max_expected_candidates=2,
    )

    assert dry.mode == "dry-run"
    assert dry.outside_target_region_count == 0
    assert dry.candidate_count == 2
    assert dry.planned_cafe_insert_count == 2
    assert dry.inserted_cafe_count == 0
    assert session.scalar(select(func.count()).select_from(Cafe)) == 0
    assert session.scalar(select(func.count()).select_from(CafeProviderPlace)) == 0

    applied = seed_kakao_catalog_expansion(
        session,
        snapshot,
        apply=True,
        max_expected_candidates=2,
        max_expected_large_moves=0,
    )

    assert applied.inserted_cafe_count == 2
    assert applied.inserted_provider_count == 2
    cafes = tuple(session.scalars(select(Cafe).order_by(Cafe.origin_source_id)))
    assert [cafe.origin_source_id for cafe in cafes] == ["100", "200"]
    assert all(cafe.origin_provider == "kakao" for cafe in cafes)
    assert all(cafe.source_release == GENERATED_AT.isoformat() for cafe in cafes)
    assert all(
        cafe.source_confidence == PROVIDER_VERIFIED_CAFE_CONFIDENCE
        for cafe in cafes
    )
    assert all(cafe.primary_category == "cafe" for cafe in cafes)
    assert cafes[0].name == "카페 100"
    assert cafes[0].lat == 37.42
    assert cafes[0].lng == 126.90
    assert cafes[0].road_address == "서울 종로구 테스트로 100"
    assert cafes[0].phone == "02-1234-5678"
    assert cafes[0].source_json == [
        {
            "provider": "kakao",
            "provider_place_id": "100",
            "category": "음식점 > 카페",
            "road_address": "서울 종로구 테스트로 100",
            "lot_address": "서울 종로구 테스트동 100",
            "phone": "02-1234-5678",
            "direct_url": "https://place.map.kakao.com/100",
        }
    ]
    links = tuple(
        session.scalars(
            select(CafeProviderPlace).order_by(
                CafeProviderPlace.provider_place_id
            )
        )
    )
    assert [link.detail_url for link in links] == [
        "https://place.map.kakao.com/100",
        "https://place.map.kakao.com/200",
    ]
    assert all(link.active for link in links)
    assert all(link.match_method == KAKAO_SOURCE_MATCH_METHOD for link in links)

    committed = False
    post_commit_selects: list[str] = []

    def mark_commit(_session: Session) -> None:
        nonlocal committed
        committed = True

    def capture_post_commit_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if committed and statement.lstrip().upper().startswith("SELECT"):
            post_commit_selects.append(statement)

    engine = session.get_bind()
    event.listen(session, "after_commit", mark_commit)
    event.listen(engine, "before_cursor_execute", capture_post_commit_select)
    try:
        repeated = seed_kakao_catalog_expansion(
            session,
            snapshot,
            apply=True,
            max_expected_candidates=2,
            max_expected_large_moves=0,
        )
    finally:
        event.remove(session, "after_commit", mark_commit)
        event.remove(engine, "before_cursor_execute", capture_post_commit_select)

    assert repeated.candidate_count == 0
    assert repeated.inserted_cafe_count == 0
    assert repeated.existing_kakao_origin_count == 2
    assert repeated.existing_provider_id_missing_from_cache_count == 0
    assert post_commit_selects == []
    assert session.scalar(select(func.count()).select_from(Cafe)) == 2
    assert session.scalar(select(func.count()).select_from(CafeProviderPlace)) == 2


def test_apply_rolls_back_cafes_when_provider_collision_fails_flush(
    session: Session,
) -> None:
    snapshot = _snapshot(_place("300", lng=126.90, lat=37.42))

    def fail_provider_flush(
        flushing_session: Session,
        flush_context: object,
        instances: object,
    ) -> None:
        del flush_context, instances
        if any(
            isinstance(item, CafeProviderPlace) for item in flushing_session.new
        ):
            raise IntegrityError("provider collision", {}, RuntimeError("collision"))

    event.listen(session, "before_flush", fail_provider_flush)
    try:
        with pytest.raises(IntegrityError, match="provider collision"):
            seed_kakao_catalog_expansion(
                session,
                snapshot,
                apply=True,
                max_expected_candidates=1,
                max_expected_large_moves=0,
            )
    finally:
        event.remove(session, "before_flush", fail_provider_flush)

    assert session.scalar(select(func.count()).select_from(Cafe)) == 0
    assert session.scalar(select(func.count()).select_from(CafeProviderPlace)) == 0


def test_apply_fails_closed_on_existing_kakao_origin_provider_collision(
    session: Session,
) -> None:
    session.add(
        Cafe(
            origin_provider="kakao",
            origin_source_id="400",
            source_release="test",
            source_confidence=1.0,
            primary_category="cafe",
            name="불완전 카페",
            lat=37.5,
            lng=127.0,
            active=True,
        )
    )
    session.commit()

    with pytest.raises(KakaoCatalogApplyError, match="origin/provider collision"):
        seed_kakao_catalog_expansion(
            session,
            _snapshot(_place("500", lng=126.90, lat=37.42)),
            apply=True,
            max_expected_candidates=1,
            max_expected_large_moves=0,
        )

    assert session.scalar(select(func.count()).select_from(Cafe)) == 1
    assert session.scalar(select(func.count()).select_from(CafeProviderPlace)) == 0


def test_apply_requires_explicit_candidate_bound_and_never_partially_writes(
    session: Session,
) -> None:
    snapshot = _snapshot(
        _place("600", lng=126.90, lat=37.42),
        _place("700", lng=127.10, lat=37.70),
    )

    with pytest.raises(KakaoCatalogApplyError, match="explicit"):
        seed_kakao_catalog_expansion(
            session,
            snapshot,
            apply=True,
            max_expected_candidates=None,
        )
    with pytest.raises(KakaoCatalogApplyError, match="exceeds operator bound"):
        seed_kakao_catalog_expansion(
            session,
            snapshot,
            apply=True,
            max_expected_candidates=1,
            max_expected_large_moves=0,
        )

    assert session.scalar(select(func.count()).select_from(Cafe)) == 0
    assert session.scalar(select(func.count()).select_from(CafeProviderPlace)) == 0


def test_missing_existing_provider_is_reported_without_deactivation(
    session: Session,
) -> None:
    cafe = Cafe(
        origin_provider="kakao",
        origin_source_id="800",
        source_release="previous",
        source_confidence=1.0,
        primary_category="cafe",
        name="기존 카페",
        lat=37.5,
        lng=127.0,
        active=True,
    )
    session.add(cafe)
    session.flush()
    link = CafeProviderPlace(
        cafe_id=cafe.id,
        provider="kakao",
        provider_place_id="800",
        detail_url="https://place.map.kakao.com/800",
        active=True,
        match_method=KAKAO_SOURCE_MATCH_METHOD,
        match_distance_m=0.0,
        verified_at=GENERATED_AT,
        last_seen_at=GENERATED_AT,
    )
    session.add(link)
    session.commit()

    report = seed_kakao_catalog_expansion(
        session,
        _snapshot(),
        apply=False,
        max_expected_candidates=0,
    )

    assert report.existing_provider_id_missing_from_cache_count == 1
    session.refresh(cafe)
    session.refresh(link)
    assert cafe.active is True
    assert link.active is True


def test_kakao_origin_refresh_quarantines_large_move_over_operator_bound(
    session: Session,
) -> None:
    cafe = Cafe(
        origin_provider="kakao",
        origin_source_id="900",
        source_release="previous",
        source_confidence=1.0,
        primary_category="cafe",
        name="이전 이름",
        lat=37.55,
        lng=126.98,
        road_address="서울 종로구 이전로 900",
        phone=None,
        active=True,
    )
    session.add(cafe)
    session.flush()
    link = CafeProviderPlace(
        cafe_id=cafe.id,
        provider="kakao",
        provider_place_id="900",
        detail_url="https://place.map.kakao.com/900",
        active=True,
        match_method=KAKAO_SOURCE_MATCH_METHOD,
        match_distance_m=0.0,
        verified_at=datetime(2026, 7, 13, tzinfo=UTC),
        last_seen_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    session.add(link)
    session.commit()
    snapshot = _snapshot(_place("900", lng=127.10, lat=37.65))

    dry = seed_kakao_catalog_expansion(
        session,
        snapshot,
        apply=False,
        max_expected_candidates=0,
        max_expected_large_moves=0,
    )

    assert dry.candidate_count == 0
    assert dry.refresh_eligible_count == 1
    assert dry.refresh_seen_count == 1
    assert dry.planned_cafe_refresh_count == 0
    assert dry.refresh_coordinate_changed_count == 1
    assert dry.refresh_large_move_count == 1
    assert dry.refresh_large_move_planned_count == 0
    assert dry.refresh_large_move_applied_count == 0
    assert dry.refresh_large_move_quarantined_count == 1
    assert (
        dry.refresh_large_move_quarantine_reason
        == KAKAO_LARGE_MOVE_QUARANTINE_REASON
    )
    assert dry.refresh_large_move_sample[0]["kakao_place_id"] == "900"
    assert dry.refresh_large_move_sample[0]["quarantined"] is True
    assert (
        dry.refresh_large_move_sample[0]["quarantine_reason"]
        == KAKAO_LARGE_MOVE_QUARANTINE_REASON
    )
    assert dry.refreshed_cafe_count == 0
    session.refresh(cafe)
    assert cafe.name == "이전 이름"
    assert cafe.lat == 37.55

    quarantined = seed_kakao_catalog_expansion(
        session,
        snapshot,
        apply=True,
        max_expected_candidates=0,
        max_expected_large_moves=0,
    )
    session.refresh(cafe)
    session.refresh(link)
    assert quarantined.mode == "write"
    assert quarantined.planned_cafe_refresh_count == 0
    assert quarantined.refreshed_cafe_count == 0
    assert quarantined.refresh_large_move_applied_count == 0
    assert quarantined.refresh_large_move_quarantined_count == 1
    assert cafe.name == "이전 이름"
    assert cafe.lat == 37.55
    assert cafe.lng == 126.98
    assert cafe.road_address == "서울 종로구 이전로 900"
    assert cafe.phone is None
    assert cafe.source_release == "previous"
    assert cafe.source_json is None
    # Suspicious movement freezes both display fields and provider verification.
    assert link.detail_url == "https://place.map.kakao.com/900"
    previous_provider_time = datetime(2026, 7, 13, tzinfo=UTC)
    assert link.last_seen_at.replace(tzinfo=UTC) == previous_provider_time
    assert link.verified_at.replace(tzinfo=UTC) == previous_provider_time

    applied = seed_kakao_catalog_expansion(
        session,
        snapshot,
        apply=True,
        max_expected_candidates=0,
        max_expected_large_moves=1,
    )

    assert applied.refreshed_cafe_count == 1
    assert applied.refresh_large_move_planned_count == 1
    assert applied.refresh_large_move_applied_count == 1
    assert applied.refresh_large_move_quarantined_count == 0
    assert applied.refresh_large_move_quarantine_reason is None
    assert applied.refresh_large_move_sample[0]["quarantined"] is False
    assert applied.refresh_large_move_sample[0]["quarantine_reason"] is None
    session.refresh(cafe)
    session.refresh(link)
    assert cafe.name == "카페 900"
    assert cafe.lat == 37.65
    assert cafe.lng == 127.10
    assert cafe.road_address == "서울 종로구 테스트로 900"
    assert cafe.phone == "02-1234-5678"
    assert cafe.source_release == GENERATED_AT.isoformat()
    assert cafe.source_json == [
        {
            "provider": "kakao",
            "provider_place_id": "900",
            "category": "음식점 > 카페",
            "road_address": "서울 종로구 테스트로 900",
            "lot_address": "서울 종로구 테스트동 900",
            "phone": "02-1234-5678",
            "direct_url": "https://place.map.kakao.com/900",
        }
    ]
    assert link.last_seen_at.replace(tzinfo=UTC) == GENERATED_AT


def test_quarantined_move_does_not_block_normal_refresh_or_new_insert(
    session: Session,
) -> None:
    suspicious, suspicious_link = _add_kakao_cafe(
        session, "910", lat=37.50, lng=126.90
    )
    normal, normal_link = _add_kakao_cafe(
        session, "911", lat=37.55, lng=127.00
    )
    session.commit()

    report = seed_kakao_catalog_expansion(
        session,
        _snapshot(
            _place("910", lng=127.10, lat=37.65),
            _place("911", lng=127.0001, lat=37.5501),
            _place("912", lng=127.10, lat=37.70),
        ),
        apply=True,
        max_expected_candidates=1,
        max_expected_large_moves=0,
    )

    assert report.refresh_large_move_count == 1
    assert report.refresh_large_move_quarantined_count == 1
    assert report.refresh_large_move_applied_count == 0
    assert report.planned_cafe_refresh_count == 1
    assert report.refreshed_cafe_count == 1
    assert report.inserted_cafe_count == 1
    assert report.inserted_provider_count == 1

    session.refresh(suspicious)
    session.refresh(suspicious_link)
    session.refresh(normal)
    session.refresh(normal_link)
    assert suspicious.name == "이전 910"
    assert suspicious.lat == 37.50
    assert suspicious.lng == 126.90
    assert suspicious.road_address == "서울 종로구 이전로 910"
    assert suspicious.phone is None
    assert suspicious.source_release == "previous"
    assert suspicious.source_json is None
    assert suspicious_link.last_seen_at.replace(tzinfo=UTC) == datetime(
        2026, 7, 13, tzinfo=UTC
    )

    assert normal.name == "카페 911"
    assert normal.lat == 37.5501
    assert normal.lng == 127.0001
    assert normal.source_release == GENERATED_AT.isoformat()
    assert normal_link.last_seen_at.replace(tzinfo=UTC) == GENERATED_AT

    inserted = session.scalar(
        select(Cafe).where(
            Cafe.origin_provider == "kakao",
            Cafe.origin_source_id == "912",
        )
    )
    assert inserted is not None
    assert inserted.name == "카페 912"
    assert inserted.lat == 37.70
    assert inserted.lng == 127.10


def test_large_move_bound_keeps_batch_all_or_none(session: Session) -> None:
    first, _ = _add_kakao_cafe(session, "920", lat=37.50, lng=126.90)
    second, _ = _add_kakao_cafe(session, "921", lat=37.51, lng=126.91)
    session.commit()

    report = seed_kakao_catalog_expansion(
        session,
        _snapshot(
            _place("920", lng=127.10, lat=37.65),
            _place("921", lng=127.11, lat=37.66),
        ),
        apply=True,
        max_expected_candidates=0,
        max_expected_large_moves=1,
    )

    assert report.refresh_large_move_count == 2
    assert report.refresh_large_move_planned_count == 0
    assert report.refresh_large_move_applied_count == 0
    assert report.refresh_large_move_quarantined_count == 2
    assert report.refreshed_cafe_count == 0
    session.refresh(first)
    session.refresh(second)
    assert (first.lat, first.lng, first.name) == (37.50, 126.90, "이전 920")
    assert (second.lat, second.lng, second.name) == (
        37.51,
        126.91,
        "이전 921",
    )
