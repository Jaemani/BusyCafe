from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import SCORING_MODEL_VERSION
from app.models import (
    Base,
    Cafe,
    CafeProviderPlace,
    CafeScore,
    Hotspot,
    HotspotParseFailure,
    HotspotSnapshot,
    IngestCycle,
)


@pytest.fixture
def engine():
    db_engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(db_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(db_engine)
    yield db_engine
    db_engine.dispose()


def hotspot(**overrides) -> Hotspot:
    values = {
        "area_cd": "POI001",
        "name": "테스트 장소",
        "lat": 37.57,
        "lng": 126.98,
    }
    values.update(overrides)
    return Hotspot(**values)


def cafe(**overrides) -> Cafe:
    values = {
        "overture_id": "overture:test-12345",
        "source_release": "2026-06-17.0",
        "source_confidence": 0.9,
        "primary_category": "cafe",
        "name": "테스트 카페",
        "lat": 37.571,
        "lng": 126.981,
    }
    values.update(overrides)
    return Cafe(**values)


def test_schema_uses_timezone_aware_datetimes_and_postgresql_jsonb():
    snapshot = HotspotSnapshot.__table__.c
    parse_failure = HotspotParseFailure.__table__.c
    score = CafeScore.__table__.c
    cycle = IngestCycle.__table__.c
    provider_place = CafeProviderPlace.__table__.c

    assert snapshot.observed_at.type.timezone is True
    assert snapshot.fetched_at.type.timezone is True
    assert parse_failure.fetched_at.type.timezone is True
    assert score.computed_at.type.timezone is True
    assert cycle.started_at.type.timezone is True
    assert cycle.completed_at.type.timezone is True
    assert provider_place.verified_at.type.timezone is True
    assert provider_place.last_seen_at.type.timezone is True
    assert score.model_version.nullable is False
    assert isinstance(
        snapshot.forecast_json.type.dialect_impl(postgresql.dialect()),
        postgresql.JSONB,
    )
    assert isinstance(
        score.contributors_json.type.dialect_impl(postgresql.dialect()),
        postgresql.JSONB,
    )
    assert isinstance(
        parse_failure.raw_json.type.dialect_impl(postgresql.dialect()),
        postgresql.JSONB,
    )


def test_complete_cycle_requires_all_targets_saved(engine):
    now = datetime(2026, 7, 12, 12, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            IngestCycle(
                started_at=now,
                completed_at=now,
                targets=2,
                saved=1,
                failed=1,
                status="complete",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_snapshot_is_unique_per_hotspot_and_observed_time(engine):
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        place = hotspot()
        session.add(place)
        session.flush()
        session.add_all(
            [
                HotspotSnapshot(
                    hotspot_id=place.id,
                    observed_at=now,
                    fetched_at=now,
                    congest_level=2,
                    congest_label="보통",
                ),
                HotspotSnapshot(
                    hotspot_id=place.id,
                    observed_at=now,
                    fetched_at=now,
                    congest_level=3,
                    congest_label="약간 붐빔",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_uncovered_score_requires_all_estimate_fields_to_be_null(engine):
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        shop = cafe()
        session.add(shop)
        session.flush()
        session.add(
            CafeScore(
                cafe_id=shop.id,
                model_version=SCORING_MODEL_VERSION,
                computed_at=now,
                coverage="uncovered",
                score=1.0,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_valid_uncovered_and_covered_scores_round_trip(engine):
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        place = hotspot()
        uncovered_cafe = cafe()
        covered_cafe = cafe(overture_id="overture:test-67890", name="커버 카페")
        session.add_all([place, uncovered_cafe, covered_cafe])
        session.flush()
        session.add_all(
            [
                CafeScore(
                    cafe_id=uncovered_cafe.id,
                    model_version=SCORING_MODEL_VERSION,
                    computed_at=now,
                    coverage="uncovered",
                ),
                CafeScore(
                    cafe_id=covered_cafe.id,
                    model_version=SCORING_MODEL_VERSION,
                    computed_at=now,
                    source_observed_at=now,
                    coverage="covered",
                    score=2.25,
                    level=2,
                    confidence=0.7,
                    confidence_tier="high",
                    primary_hotspot_id=place.id,
                    primary_distance_m=125.0,
                    contributors_json=[
                        {
                            "hotspot_id": place.id,
                            "distance_m": 125.0,
                            "level": 2,
                            "weight": 1.0,
                        }
                    ],
                ),
            ]
        )
        session.commit()

        stored = session.scalars(select(CafeScore).order_by(CafeScore.cafe_id)).all()
        assert stored[0].coverage == "uncovered"
        assert stored[0].score is None
        assert stored[1].contributors_json[0]["level"] == 2


@pytest.mark.parametrize(
    ("coverage", "source_observed_at"),
    [("uncovered", datetime(2026, 7, 11, 12, tzinfo=UTC)), ("covered", None)],
)
def test_score_source_observation_matches_coverage(
    engine, coverage: str, source_observed_at: datetime | None
):
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        place = hotspot()
        shop = cafe()
        session.add_all([place, shop])
        session.flush()
        estimate_fields = (
            {
                "score": 2.0,
                "level": 2,
                "confidence": 0.5,
                "confidence_tier": "mid",
                "primary_hotspot_id": place.id,
                "primary_distance_m": 100.0,
                "contributors_json": [],
            }
            if coverage == "covered"
            else {}
        )
        session.add(
            CafeScore(
                cafe_id=shop.id,
                model_version=SCORING_MODEL_VERSION,
                computed_at=now,
                source_observed_at=source_observed_at,
                coverage=coverage,
                **estimate_fields,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_cafe_delete_cascades_materialized_score(engine):
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        shop = cafe()
        session.add(shop)
        session.flush()
        session.add(
            CafeScore(
                cafe_id=shop.id,
                model_version=SCORING_MODEL_VERSION,
                computed_at=now,
                coverage="uncovered",
            )
        )
        session.commit()
        shop_id = shop.id
        session.delete(shop)
        session.commit()

        assert session.get(CafeScore, shop_id) is None


def test_provider_only_cafe_and_provider_identity_round_trip(engine):
    now = datetime(2026, 7, 13, 12, tzinfo=UTC)
    with Session(engine) as session:
        shop = cafe(
            overture_id=None,
            origin_provider="kakao",
            origin_source_id="12345",
            source_release="2026-07-13",
        )
        shop.provider_places.append(
            CafeProviderPlace(
                provider="kakao",
                provider_place_id="12345",
                detail_url="https://place.map.kakao.com/12345",
                match_method="source_primary",
                verified_at=now,
                last_seen_at=now,
            )
        )
        session.add(shop)
        session.commit()

        stored = session.scalar(select(Cafe).where(Cafe.id == shop.id))
        assert stored is not None
        assert stored.overture_id is None
        assert stored.origin_provider == "kakao"
        assert stored.provider_places[0].provider_place_id == "12345"

        session.delete(stored)
        session.commit()
        assert session.scalars(select(CafeProviderPlace)).all() == []


def test_provider_identity_uniqueness_fails_closed(engine):
    now = datetime(2026, 7, 13, 12, tzinfo=UTC)
    with Session(engine) as session:
        first = cafe()
        second = cafe(overture_id="overture:second", name="둘째")
        session.add_all([first, second])
        session.flush()
        session.add_all(
            [
                CafeProviderPlace(
                    cafe_id=first.id,
                    provider="naver",
                    provider_place_id="777",
                    match_method="exact_id",
                    verified_at=now,
                    last_seen_at=now,
                ),
                CafeProviderPlace(
                    cafe_id=second.id,
                    provider="naver",
                    provider_place_id="777",
                    match_method="exact_id",
                    verified_at=now,
                    last_seen_at=now,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_parse_failures_are_append_only_and_hotspot_delete_cascades(engine):
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        place = hotspot()
        session.add(place)
        session.flush()
        session.add_all(
            [
                HotspotParseFailure(
                    hotspot_id=place.id,
                    fetched_at=now,
                    error_type="ValidationError",
                    error_message="required population field is missing",
                    raw_json={"AREA_NM": "테스트 장소"},
                ),
                HotspotParseFailure(
                    hotspot_id=place.id,
                    fetched_at=now,
                    error_type="ValidationError",
                    error_message="required population field is missing",
                    raw_json={"AREA_NM": "테스트 장소"},
                ),
            ]
        )
        session.commit()

        failures = session.scalars(select(HotspotParseFailure)).all()
        assert len(failures) == 2
        assert failures[0].raw_json["AREA_NM"] == "테스트 장소"

        session.delete(place)
        session.commit()
        assert session.scalars(select(HotspotParseFailure)).all() == []


def test_parse_failure_requires_nonempty_error_summary(engine):
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    with Session(engine) as session:
        place = hotspot()
        session.add(place)
        session.flush()
        session.add(
            HotspotParseFailure(
                hotspot_id=place.id,
                fetched_at=now,
                error_type="",
                error_message="invalid response",
                raw_json={},
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_expected_query_indexes_exist(engine):
    schema = inspect(engine)
    cafe_indexes = {item["name"] for item in schema.get_indexes("cafes")}
    snapshot_indexes = {
        item["name"] for item in schema.get_indexes("hotspot_snapshots")
    }
    failure_indexes = {
        item["name"] for item in schema.get_indexes("hotspot_parse_failures")
    }

    assert "ix_cafes_bbox" in cafe_indexes
    assert "ix_cafes_active_bbox" in cafe_indexes
    assert "ix_snap_hotspot_time" in snapshot_indexes
    assert "ix_snap_fetched_at" in snapshot_indexes
    assert "ix_parse_failure_hotspot_time" in failure_indexes
