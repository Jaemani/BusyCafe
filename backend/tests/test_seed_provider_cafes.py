from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.ingest.provider_cafe_catalog import (
    ProviderCatalogRecords,
    ProviderNeutralCafeCandidate,
    ProviderReference,
)
from app.models import Base, Cafe, CafeProviderPlace
from scripts.seed_provider_cafes import ProviderSeedError, main, seed_provider_cafes


NOW = datetime(2026, 7, 13, 3, 0, tzinfo=UTC)


def _cafe(origin: str, identifier: str, *, overture_id: str | None = None) -> Cafe:
    return Cafe(
        origin_provider=origin,
        origin_source_id=identifier,
        overture_id=overture_id,
        source_release="test",
        source_confidence=0.9,
        primary_category="cafe",
        name=f"카페 {identifier}",
        lat=37.55,
        lng=126.98,
        active=True,
    )


def _reference(
    source: str,
    source_id: str,
    place_id: str,
) -> ProviderReference:
    return ProviderReference(
        canonical_source=source,
        canonical_source_id=source_id,
        provider="kakao",
        provider_place_id=place_id,
        direct_url=f"https://place.map.kakao.com/{place_id}",
        match_rule="exact_name",
        match_distance_m=4.5,
    )


def _candidate(
    identifier: str = "permit-1",
    *,
    place_id: str = "200",
) -> ProviderNeutralCafeCandidate:
    reference = _reference(
        "seoul_refreshment_permits", identifier, place_id
    )
    return ProviderNeutralCafeCandidate(
        canonical_source="seoul_refreshment_permits",
        canonical_source_id=identifier,
        name="새 카페",
        latitude=37.56,
        longitude=126.99,
        category="커피숍",
        road_address="서울 테스트로 1",
        lot_address="서울 테스트동 1",
        phone="0212345678",
        provider_refs=(reference,),
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def test_seed_dry_run_then_apply_is_additive_and_idempotent(session: Session) -> None:
    existing = _cafe("overture", "ov-1", overture_id="ov-1")
    session.add(existing)
    session.commit()
    catalog = ProviderCatalogRecords(
        existing_provider_refs=(_reference("overture", "ov-1", "100"),),
        new_cafe_candidates=(_candidate(),),
    )

    dry = seed_provider_cafes(session, catalog, dry_run=True, now=NOW)

    assert dry.cafe_inserted_count == 1
    assert dry.provider_inserted_count == 2
    assert dry.provider_deactivated_count == 0
    assert dry.cafe_deactivated_count == 0
    assert session.scalar(select(Cafe).where(Cafe.origin_source_id == "permit-1")) is None

    applied = seed_provider_cafes(session, catalog, dry_run=False, now=NOW)

    assert applied.cafe_inserted_count == 1
    cafes = tuple(session.scalars(select(Cafe).order_by(Cafe.id)))
    assert len(cafes) == 2
    created = cafes[1]
    assert created.origin_provider == "seoul_refreshment_permits"
    assert created.origin_source_id == "permit-1"
    assert created.overture_id is None
    assert created.name == "새 카페"
    assert created.source_json == [
        {
            "dataset_id": "OA-16095",
            "management_number": "permit-1",
            "provenance": "official_open_refreshment_permit",
            "provider_confirmation": [
                {
                    "provider": "kakao",
                    "provider_place_id": "200",
                    "match_rule": "exact_name",
                    "distance_m": 4.5,
                }
            ],
        }
    ]
    places = tuple(session.scalars(select(CafeProviderPlace)))
    assert {(place.provider, place.provider_place_id) for place in places} == {
        ("kakao", "100"),
        ("kakao", "200"),
    }

    repeated = seed_provider_cafes(session, catalog, dry_run=True, now=NOW)
    assert repeated.cafe_inserted_count == 0
    assert repeated.cafe_unchanged_count == 1
    assert repeated.provider_inserted_count == 0
    assert repeated.provider_unchanged_count == 2
    assert repeated.provider_deactivated_count == 0
    assert repeated.cafe_deactivated_count == 0


def test_seed_flushes_new_cafes_as_one_batch_before_provider_links(
    session: Session,
) -> None:
    catalog = ProviderCatalogRecords(
        existing_provider_refs=(),
        new_cafe_candidates=tuple(
            _candidate(f"permit-{index}", place_id=str(200 + index))
            for index in range(3)
        ),
    )
    flush_batches: list[tuple[int, int]] = []

    def capture_flush(
        flushing_session: Session,
        flush_context: object,
        instances: object,
    ) -> None:
        del flush_context, instances
        flush_batches.append(
            (
                sum(
                    isinstance(item, Cafe) for item in flushing_session.new
                ),
                sum(
                    isinstance(item, CafeProviderPlace)
                    for item in flushing_session.new
                ),
            )
        )

    event.listen(session, "before_flush", capture_flush)
    try:
        report = seed_provider_cafes(
            session, catalog, dry_run=False, now=NOW
        )
    finally:
        event.remove(session, "before_flush", capture_flush)

    assert report.cafe_inserted_count == 3
    assert report.provider_inserted_count == 3
    assert flush_batches == [(3, 0), (0, 3)]


def test_seed_updates_permit_origin_and_provider_without_deactivation(
    session: Session,
) -> None:
    existing = _cafe("seoul_refreshment_permits", "permit-1")
    existing.name = "옛 이름"
    session.add(existing)
    session.flush()
    session.add(
        CafeProviderPlace(
            cafe_id=existing.id,
            provider="kakao",
            provider_place_id="200",
            detail_url="https://place.map.kakao.com/200",
            active=False,
            match_method="old",
            match_distance_m=10.0,
            verified_at=NOW,
            last_seen_at=NOW,
        )
    )
    unrelated = _cafe("overture", "unrelated", overture_id="unrelated")
    unrelated.active = True
    session.add(unrelated)
    session.commit()

    report = seed_provider_cafes(
        session,
        ProviderCatalogRecords((), (_candidate(),)),
        dry_run=False,
        now=NOW,
    )

    assert report.cafe_updated_count == 1
    assert report.provider_updated_count == 1
    assert report.cafe_deactivated_count == 0
    assert unrelated.active is True
    assert existing.name == "새 카페"
    assert existing.provider_places[0].active is True
    assert existing.provider_places[0].match_method == "exact_name"


def test_seed_rejects_missing_canonical_target_before_write(session: Session) -> None:
    catalog = ProviderCatalogRecords(
        existing_provider_refs=(_reference("overture", "missing", "100"),),
        new_cafe_candidates=(),
    )

    with pytest.raises(ProviderSeedError, match="missing canonical cafe"):
        seed_provider_cafes(session, catalog, dry_run=False, now=NOW)

    assert session.scalar(select(CafeProviderPlace)) is None


def test_seed_rejects_provider_identity_owned_by_other_cafe(session: Session) -> None:
    first = _cafe("overture", "ov-1", overture_id="ov-1")
    second = _cafe("overture", "ov-2", overture_id="ov-2")
    session.add_all((first, second))
    session.flush()
    session.add(
        CafeProviderPlace(
            cafe_id=second.id,
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

    catalog = ProviderCatalogRecords(
        existing_provider_refs=(_reference("overture", "ov-1", "100"),),
        new_cafe_candidates=(),
    )
    with pytest.raises(ProviderSeedError, match="belongs to another"):
        seed_provider_cafes(session, catalog, dry_run=False, now=NOW)

    assert len(tuple(session.scalars(select(CafeProviderPlace)))) == 1


def test_seed_rejects_duplicate_incoming_reference_before_write(
    session: Session,
) -> None:
    existing = _cafe("overture", "ov-1", overture_id="ov-1")
    session.add(existing)
    session.commit()
    reference = _reference("overture", "ov-1", "100")
    catalog = ProviderCatalogRecords(
        existing_provider_refs=(reference, reference),
        new_cafe_candidates=(),
    )

    with pytest.raises(ProviderSeedError, match="duplicated"):
        seed_provider_cafes(session, catalog, dry_run=False, now=NOW)

    assert session.scalar(select(CafeProviderPlace)) is None


@pytest.mark.parametrize(
    "match_method",
    [
        "exact_name",
        "exact_phone",
        "exact_name_and_phone",
        "exact_name_and_address",
        "exact_phone_and_address",
        "exact_name_and_phone_and_address",
    ],
)
def test_seed_retires_only_absent_managed_kakao_references(
    session: Session,
    match_method: str,
) -> None:
    cafes = [
        _cafe("overture", "managed", overture_id="managed"),
        _cafe("overture", "direct", overture_id="direct"),
        _cafe("overture", "primary", overture_id="primary"),
        _cafe("overture", "other", overture_id="other"),
    ]
    session.add_all(cafes)
    session.flush()
    places = [
        CafeProviderPlace(
            cafe_id=cafes[0].id,
            provider="kakao",
            provider_place_id="100",
            detail_url="https://place.map.kakao.com/100",
            active=True,
            match_method=match_method,
            verified_at=NOW,
            last_seen_at=NOW,
        ),
        CafeProviderPlace(
            cafe_id=cafes[1].id,
            provider="kakao",
            provider_place_id="200",
            detail_url="https://place.map.kakao.com/200",
            active=True,
            match_method="source_direct_url",
            verified_at=NOW,
            last_seen_at=NOW,
        ),
        CafeProviderPlace(
            cafe_id=cafes[2].id,
            provider="overture",
            provider_place_id="primary",
            detail_url=None,
            active=True,
            match_method="source_primary",
            verified_at=NOW,
            last_seen_at=NOW,
        ),
        CafeProviderPlace(
            cafe_id=cafes[3].id,
            provider="naver",
            provider_place_id="300",
            detail_url="https://map.naver.com/p/entry/place/300",
            active=True,
            match_method=match_method,
            verified_at=NOW,
            last_seen_at=NOW,
        ),
    ]
    session.add_all(places)
    session.commit()
    empty = ProviderCatalogRecords((), ())

    dry = seed_provider_cafes(session, empty, dry_run=True, now=NOW)

    assert dry.provider_deactivated_count == 1
    assert all(place.active for place in places)
    assert all(cafe.active for cafe in cafes)

    applied = seed_provider_cafes(session, empty, dry_run=False, now=NOW)

    assert applied.provider_deactivated_count == 1
    assert places[0].active is False
    assert all(place.active for place in places[1:])
    assert all(cafe.active for cafe in cafes)

    repeated = seed_provider_cafes(session, empty, dry_run=False, now=NOW)
    assert repeated.provider_deactivated_count == 0


def test_seed_keeps_managed_kakao_reference_present_in_catalog(
    session: Session,
) -> None:
    cafe = _cafe("overture", "ov-1", overture_id="ov-1")
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
            match_distance_m=4.5,
            verified_at=NOW,
            last_seen_at=NOW,
        )
    )
    session.commit()

    report = seed_provider_cafes(
        session,
        ProviderCatalogRecords(
            (_reference("overture", "ov-1", "100"),), ()
        ),
        dry_run=False,
        now=NOW,
    )

    assert report.provider_deactivated_count == 0
    assert cafe.provider_places[0].active is True


def test_cli_requires_manifest_before_opening_database(
    tmp_path, monkeypatch, capsys
) -> None:
    cache = tmp_path / "provider.jsonl"
    cache.write_text("", encoding="utf-8")

    def fail_engine(*args: object, **kwargs: object) -> None:
        raise AssertionError("database must not open before manifest validation")

    monkeypatch.setattr("scripts.seed_provider_cafes.create_db_engine", fail_engine)

    assert main(["--cache", str(cache)]) == 1
    assert "manifest" in capsys.readouterr().err


def test_cli_accepts_explicit_manifest_path(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "provider.jsonl"
    manifest = tmp_path / "custom-manifest.json"
    seen: list[tuple[object, object]] = []

    def stop_after_read(cache_path, manifest_path):
        seen.append((cache_path, manifest_path))
        raise RuntimeError("stop")

    monkeypatch.setattr(
        "scripts.seed_provider_cafes.read_complete_provider_catalog",
        stop_after_read,
    )

    assert main(
        ["--cache", str(cache), "--manifest", str(manifest)]
    ) == 1
    assert seen == [(cache, manifest)]
