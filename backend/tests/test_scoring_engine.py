from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.config import SCORING_MODEL_VERSION
from app.models import (
    Base,
    Cafe,
    CafeScore,
    Hotspot,
    HotspotServingState,
    HotspotSnapshot,
)
from app.scoring.engine import HotspotObservation, materialize_all, score_cafe


NOW = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)


def observation(
    hotspot_id: int,
    *,
    lat: float = 37.0,
    lng: float = 127.0,
    level: int = 1,
    age_minutes: float = 0,
) -> HotspotObservation:
    return HotspotObservation(
        hotspot_id=hotspot_id,
        name=f"핫스팟 {hotspot_id}",
        lat=lat,
        lng=lng,
        level=level,
        observed_at=NOW - timedelta(minutes=age_minutes),
    )


def test_no_neighbor_is_fully_uncovered_and_has_no_evidence() -> None:
    result = score_cafe(
        37.0,
        127.0,
        [observation(1, lng=128.0)],
        now=NOW,
        r_max_m=1_000,
    )

    assert result.coverage == "uncovered"
    assert result.score is None
    assert result.level is None
    assert result.confidence is None
    assert result.confidence_tier is None
    assert result.primary_hotspot_id is None
    assert result.primary_distance_m is None
    assert result.contributors is None


def test_distance_floor_prevents_near_zero_hotspot_from_exploding() -> None:
    result = score_cafe(
        37.0,
        127.0,
        [
            observation(1, level=1),
            observation(2, lng=127.00001, level=3),
        ],
        now=NOW,
        d_floor_m=50,
    )

    assert result.score == pytest.approx(2.0)
    assert result.level == 2
    assert result.contributors is not None
    assert [item.weight for item in result.contributors] == pytest.approx([0.5, 0.5])


def test_idw_selects_nearest_k_and_uses_half_up_display_level() -> None:
    result = score_cafe(
        37.0,
        127.0,
        [
            observation(3, lng=127.003, level=4),
            observation(2, lng=127.002, level=3),
            observation(1, lng=127.001, level=2),
        ],
        now=NOW,
        k_neighbors=2,
        d_floor_m=1,
    )

    assert result.contributors is not None
    assert [item.hotspot_id for item in result.contributors] == [1, 2]
    assert result.score == pytest.approx(2.2, abs=0.01)
    assert result.level == 2
    assert sum(item.weight for item in result.contributors) == pytest.approx(1.0)


def test_coverage_boundary_and_confidence_tiers() -> None:
    covered = score_cafe(
        37.0,
        127.0,
        [observation(1, lng=127.001), observation(2, lng=127.002)],
        now=NOW,
        r_max_m=1_000,
        covered_m=100,
    )
    fringe = score_cafe(
        37.0,
        127.0,
        [observation(1, lng=127.002), observation(2, lng=127.003)],
        now=NOW,
        r_max_m=1_000,
        covered_m=100,
    )

    assert covered.coverage == "covered"
    assert covered.confidence_tier == "high"
    assert fringe.coverage == "fringe"
    assert fringe.confidence_tier in {"high", "mid"}


def test_stale_data_decays_confidence_without_changing_score() -> None:
    fresh = score_cafe(
        37.0,
        127.0,
        [observation(1, level=4), observation(2, lng=127.001, level=2)],
        now=NOW,
    )
    stale = score_cafe(
        37.0,
        127.0,
        [
            observation(1, level=4, age_minutes=150),
            observation(2, lng=127.001, level=2, age_minutes=150),
        ],
        now=NOW,
    )

    assert stale.score == pytest.approx(fresh.score)
    assert stale.confidence is not None and fresh.confidence is not None
    assert stale.confidence < fresh.confidence / 1_000
    assert stale.confidence_tier == "low"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"r_max_m": 0}, "r_max_m"),
        ({"covered_m": 2_000, "r_max_m": 1_000}, "covered_m"),
        ({"k_neighbors": 0}, "k_neighbors"),
        ({"d_floor_m": 0}, "d_floor_m"),
        ({"tau_min": 0}, "tau_min"),
        ({"conf_mid": 0.8, "conf_high": 0.5}, "confidence thresholds"),
    ],
)
def test_invalid_parameters_fail_closed(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        score_cafe(37.0, 127.0, [], now=NOW, **kwargs)


def test_naive_timestamps_and_invalid_levels_are_rejected() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        score_cafe(37.0, 127.0, [], now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="level"):
        score_cafe(37.0, 127.0, [observation(1, level=5)], now=NOW)
    naive = observation(1)
    naive = HotspotObservation(
        hotspot_id=naive.hotspot_id,
        name=naive.name,
        lat=naive.lat,
        lng=naive.lng,
        level=naive.level,
        observed_at=naive.observed_at.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="observed_at"):
        score_cafe(37.0, 127.0, [naive], now=NOW)


def test_materialize_all_upserts_active_cafes_and_recomputes_in_place() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        hotspot = Hotspot(
            area_cd="POI001",
            name="테스트 핫스팟",
            lat=37.0,
            lng=127.0,
            is_polled=True,
        )
        cafe = Cafe(
            overture_id="overture:test",
            source_release="2026-06-17.0",
            source_confidence=0.9,
            primary_category="cafe",
            name="테스트 카페",
            lat=37.0,
            lng=127.0,
        )
        second_cafe = Cafe(
            overture_id="overture:test:second",
            source_release="2026-06-17.0",
            source_confidence=0.9,
            primary_category="cafe",
            name="두 번째 테스트 카페",
            lat=37.0001,
            lng=127.0001,
        )
        session.add_all([hotspot, cafe, second_cafe])
        session.flush()
        session.add(
            HotspotSnapshot(
                hotspot_id=hotspot.id,
                observed_at=NOW,
                fetched_at=NOW,
                congest_level=3,
                congest_label="약간 붐빔",
                forecast_json=[
                    {
                        "FCST_TIME": "2026-07-11 19:00",
                        "FCST_CONGEST_LVL": "보통",
                    }
                ],
            )
        )
        session.commit()

        first = materialize_all(session, now=NOW)
        selected_statements: list[str] = []
        score_update_batches: list[bool] = []

        def capture_selects(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                selected_statements.append(statement.lower())
            if statement.lstrip().upper().startswith("UPDATE CAFE_SCORES"):
                score_update_batches.append(_executemany)

        event.listen(engine, "before_cursor_execute", capture_selects)
        second = materialize_all(session, now=NOW + timedelta(minutes=15))
        event.remove(engine, "before_cursor_execute", capture_selects)

        assert first.cafes == second.cafes == 2
        assert first.covered == 2
        stored = session.scalar(select(CafeScore))
        assert stored is not None
        assert stored.model_version == SCORING_MODEL_VERSION
        assert stored.level == 3
        assert stored.coverage == "covered"
        assert stored.computed_at.replace(tzinfo=UTC) == NOW + timedelta(minutes=15)
        assert stored.source_observed_at.replace(tzinfo=UTC) == NOW
        serving_state = session.get(HotspotServingState, hotspot.id)
        assert serving_state is not None
        assert serving_state.observed_at.replace(tzinfo=UTC) == NOW
        assert serving_state.trend_12h_json == [
            {"observed_at": NOW.isoformat(), "level": 3}
        ]
        assert serving_state.forecast_1h_json == {
            "FCST_TIME": "2026-07-11 19:00",
            "FCST_CONGEST_LVL": "보통",
        }
        assert len(session.scalars(select(CafeScore)).all()) == 2
        materialize_selects = "\n".join(selected_statements)
        assert "cafes.source_json" not in materialize_selects
        assert "cafe_scores.contributors_json" not in materialize_selects
        assert "hotspot_snapshots.raw_json" not in materialize_selects
        assert "hotspot_snapshots.forecast_json" in materialize_selects
        assert score_update_batches == [True]
    engine.dispose()


def test_materialize_uncovered_persists_sql_null_contributors() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Cafe(
                overture_id="overture:uncovered",
                source_release="2026-06-17.0",
                source_confidence=0.9,
                primary_category="cafe",
                name="미커버 카페",
                lat=37.0,
                lng=127.0,
            )
        )
        session.commit()

        report = materialize_all(session, now=NOW)

        stored = session.scalar(select(CafeScore))
        assert report.uncovered == 1
        assert stored is not None
        assert stored.coverage == "uncovered"
        assert stored.contributors_json is None
    engine.dispose()
