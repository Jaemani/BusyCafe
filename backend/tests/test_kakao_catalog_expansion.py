from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.ingest.kakao_catalog_expansion import (
    CanonicalCafeIdentity,
    build_kakao_expansion,
)
from app.models import Base, Cafe, CafeProviderPlace
from app.schemas import KakaoPlace
from scripts import report_kakao_catalog_expansion


NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _kakao(identifier: str, **overrides: object) -> KakaoPlace:
    values: dict[str, object] = {
        "id": identifier,
        "place_name": f"카페 {identifier}",
        "category_name": "음식점 > 카페",
        "category_group_code": "CE7",
        "category_group_name": "카페",
        "phone": "",
        "address_name": f"서울 종로구 테스트동 {identifier}",
        "road_address_name": f"서울 종로구 테스트로 {identifier}",
        "x": 126.98,
        "y": 37.55,
        "place_url": f"http://place.map.kakao.com/{identifier}",
        "distance": "",
    }
    values.update(overrides)
    return KakaoPlace.model_validate(values)


def _canonical() -> CanonicalCafeIdentity:
    return CanonicalCafeIdentity(
        canonical_id=1,
        name="기존 카페",
        latitude=37.55,
        longitude=126.98,
        road_address="서울 종로구 기존로 1",
        phone="02-1234-5678",
    )


def test_expansion_uses_kakao_identity_and_quarantines_collisions() -> None:
    records = (
        _kakao("100", x=126.90, y=37.40),
        _kakao("200", x=127.10, y=37.70),
        _kakao("300", place_name="기존카페", x=126.98, y=37.5502),
        _kakao("301", phone="02-1234-5678", x=126.98, y=37.5508),
        _kakao("302", x=126.98, y=37.55),
        _kakao("400", place_name="중복 후보", x=127.01, y=37.60),
        _kakao("401", place_name="중복후보", x=127.01, y=37.6002),
        _kakao("500", place_name="좌표 A", x=127.02, y=37.61),
        _kakao("501", place_name="좌표 B", x=127.02, y=37.61),
        _kakao("600", x=127.03, y=37.62),
        _kakao("600", x=127.03, y=37.62),
    )

    build = build_kakao_expansion(records, (_canonical(),), ("100",))

    assert [item.canonical_source_id for item in build.candidates] == ["200"]
    candidate = build.candidates[0]
    assert candidate.canonical_source == "kakao"
    assert candidate.direct_url == "https://place.map.kakao.com/200"
    assert build.report.kakao_input_count == 11
    assert build.report.unique_kakao_place_count == 9
    assert build.report.duplicate_kakao_place_id_count == 1
    assert build.report.existing_provider_id_in_cache_count == 1
    assert build.report.unmatched_kakao_place_count == 8
    assert build.report.canonical_collision_count == 3
    assert build.report.peer_collision_count == 4
    assert build.report.conflict_count == 7
    assert build.report.candidate_count == 1
    assert build.report.human_apply_required is True


def test_expansion_is_order_independent() -> None:
    records = (
        _kakao("100", x=126.90, y=37.40),
        _kakao("200", x=127.10, y=37.70),
        _kakao("300", place_name="기존카페", x=126.98, y=37.5502),
    )

    first = build_kakao_expansion(records, (_canonical(),), ("100",))
    second = build_kakao_expansion(
        tuple(reversed(records)), (_canonical(),), ("100",)
    )

    assert first == second


def test_expansion_rejects_invalid_provider_ownership_inputs() -> None:
    with pytest.raises(ValueError, match="duplicate existing Kakao"):
        build_kakao_expansion((), (), ("100", "100"))
    with pytest.raises(ValueError, match="not CE7"):
        build_kakao_expansion(
            (_kakao("100", category_group_code="FD6"),), (), ()
        )


def test_database_report_is_read_only_and_exposes_human_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        cafe = Cafe(
            origin_provider="overture",
            origin_source_id="ov-1",
            overture_id="ov-1",
            source_release="test",
            source_confidence=0.9,
            primary_category="cafe",
            name="기존 카페",
            lat=37.55,
            lng=126.98,
            road_address="서울 종로구 기존로 1",
            phone="02-1234-5678",
            active=True,
        )
        session.add(cafe)
        session.flush()
        session.add(
            CafeProviderPlace(
                cafe_id=cafe.id,
                provider="kakao",
                provider_place_id="100",
                detail_url="https://place.map.kakao.com/100",
                active=True,
                match_method="exact_name",
                match_distance_m=1.0,
                verified_at=NOW,
                last_seen_at=NOW,
            )
        )
        session.commit()
        monkeypatch.setattr(
            report_kakao_catalog_expansion,
            "read_kakao_cache",
            lambda cache, manifest: (
                _kakao("100", x=126.90, y=37.40),
                _kakao("200", x=127.10, y=37.70),
            ),
        )

        payload = report_kakao_catalog_expansion.build_database_report(
            session,
            kakao_cache=Path("unused.jsonl"),
            kakao_manifest=Path("unused.manifest.json"),
        )

        assert payload["mode"] == "read-only-dry-run"
        assert payload["canonical_source_for_candidates"] == "kakao"
        assert payload["report"]["candidate_count"] == 1
        assert payload["candidate_sample"][0]["canonical_source_id"] == "200"
        assert "HUMAN" in payload["apply_gate"]
        assert session.scalar(select(func.count()).select_from(Cafe)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CafeProviderPlace))
            == 1
        )
    engine.dispose()
