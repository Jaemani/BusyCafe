from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import FIXTURES_DIR, NOWCAST_SHADOW_MODEL_VERSION
from app.models import Base, Hotspot, HotspotSnapshot
from app.scoring.nowcast_shadow import (
    NowcastSnapshot,
    backtest_nowcasts,
    estimate_nowcast,
    parse_forecast_points,
)
from scripts.run_nowcast_backtest import load_snapshots


def forecast(
    at: str,
    *,
    label: str,
    population_min: int,
    population_max: int,
) -> dict[str, object]:
    return {
        "FCST_TIME": at,
        "FCST_CONGEST_LVL": label,
        "FCST_PPLTN_MIN": str(population_min),
        "FCST_PPLTN_MAX": str(population_max),
    }


def snapshot(
    *,
    hotspot_id: int = 1,
    observed_at: datetime,
    fetched_at: datetime,
    level: int,
    population_min: int,
    population_max: int,
    forecasts: tuple[dict[str, object], ...] = (),
) -> NowcastSnapshot:
    return NowcastSnapshot(
        hotspot_id=hotspot_id,
        observed_at=observed_at,
        fetched_at=fetched_at,
        level=level,
        population_min=population_min,
        population_max=population_max,
        forecast_json=forecasts,
    )


def test_real_fixture_interpolates_observation_to_historical_fetch_time() -> None:
    payload = json.loads((FIXTURES_DIR / "citydata_sample.json").read_text())
    area = payload["SeoulRtd.citydata_ppltn"][0]
    origin = snapshot(
        observed_at=datetime(2026, 7, 11, 7, 25, tzinfo=UTC),
        fetched_at=datetime(2026, 7, 11, 7, 50, tzinfo=UTC),
        level=2,
        population_min=6500,
        population_max=7000,
        forecasts=tuple(area["FCST_PPLTN"]),
    )

    result = estimate_nowcast(origin)

    assert result is not None
    assert result.method == "interpolated"
    assert result.target_at == origin.fetched_at
    assert result.level == 2
    assert result.population_min == pytest.approx(6500)
    assert result.population_max == pytest.approx(7000)


def test_interpolates_population_and_ordinal_level_between_anchors() -> None:
    origin = snapshot(
        observed_at=datetime(2026, 7, 11, 7, 0, tzinfo=UTC),
        fetched_at=datetime(2026, 7, 11, 7, 30, tzinfo=UTC),
        level=1,
        population_min=100,
        population_max=200,
        forecasts=(
            forecast(
                "2026-07-11 17:00",
                label="약간 붐빔",
                population_min=300,
                population_max=400,
            ),
        ),
    )

    result = estimate_nowcast(origin)

    assert result is not None
    assert result.method == "interpolated"
    assert result.level_score == pytest.approx(2.0)
    assert result.level == 2
    assert result.population_min == pytest.approx(200)
    assert result.population_max == pytest.approx(300)


def test_uses_nearest_forecast_only_within_explicit_tolerance() -> None:
    origin = snapshot(
        observed_at=datetime(2026, 7, 11, 7, 0, tzinfo=UTC),
        fetched_at=datetime(2026, 7, 11, 8, 10, tzinfo=UTC),
        level=1,
        population_min=100,
        population_max=200,
        forecasts=(
            forecast(
                "2026-07-11 17:00",
                label="약간 붐빔",
                population_min=300,
                population_max=400,
            ),
        ),
    )

    result = estimate_nowcast(origin, nearest_tolerance_min=10)
    missing = estimate_nowcast(origin, nearest_tolerance_min=9.9)

    assert result is not None
    assert result.method == "nearest"
    assert result.level == 3
    assert missing is None


def test_parser_rejects_malformed_or_conflicting_points() -> None:
    with pytest.raises(ValueError, match="invalid forecast point"):
        parse_forecast_points(({"FCST_TIME": "bad"},))

    first = forecast(
        "2026-07-11 17:00",
        label="보통",
        population_min=100,
        population_max=200,
    )
    conflicting = forecast(
        "2026-07-11 17:00",
        label="붐빔",
        population_min=300,
        population_max=400,
    )
    with pytest.raises(ValueError, match="conflicting duplicate"):
        parse_forecast_points((first, conflicting))


def backtest_pair() -> tuple[NowcastSnapshot, NowcastSnapshot]:
    observed = datetime(2026, 7, 11, 7, 0, tzinfo=UTC)
    origin = snapshot(
        observed_at=observed,
        fetched_at=observed + timedelta(minutes=30),
        level=1,
        population_min=100,
        population_max=100,
        forecasts=(
            forecast(
                "2026-07-11 17:00",
                label="약간 붐빔",
                population_min=300,
                population_max=300,
            ),
        ),
    )
    actual = snapshot(
        observed_at=observed + timedelta(minutes=30),
        fetched_at=observed + timedelta(hours=1),
        level=2,
        population_min=200,
        population_max=200,
    )
    return origin, actual


def test_backtest_compares_forecast_with_actual_that_arrived_later() -> None:
    report = backtest_nowcasts(
        backtest_pair(),
        min_samples=1,
        min_hotspots=1,
        min_span_days=0,
        max_population_wape=0,
        min_level_exact_accuracy=1,
        min_level_adjacent_accuracy=1,
    )

    assert report.model_version == NOWCAST_SHADOW_MODEL_VERSION
    assert report.samples_evaluated == 1
    assert report.mean_observation_lag_min == pytest.approx(30)
    assert report.nowcast_population_mae == pytest.approx(0)
    assert report.baseline_population_mae == pytest.approx(100)
    assert report.nowcast_level_exact_accuracy == pytest.approx(1)
    assert report.baseline_level_exact_accuracy == pytest.approx(0)
    assert report.quality_passed is True
    assert report.promotion_eligible is True


def test_default_gate_blocks_small_or_short_backtest_even_when_perfect() -> None:
    report = backtest_nowcasts(backtest_pair())

    assert report.sample_sufficient is False
    assert report.quality_passed is True
    assert report.promotion_eligible is False
    assert [bucket.label for bucket in report.lag_buckets] == [
        "<=15",
        "15-30",
        "30-60",
        "60-120",
        ">120",
    ]
    assert [bucket.samples for bucket in report.lag_buckets] == [0, 1, 0, 0, 0]
    thirty_minute = report.lag_buckets[1]
    assert thirty_minute.population_mae_delta == pytest.approx(-100)
    assert thirty_minute.level_exact_accuracy_delta == pytest.approx(1)
    assert report.promotion_blockers[:3] == (
        "insufficient_samples",
        "insufficient_hotspots",
        "insufficient_span",
    )


def test_backtest_never_uses_actual_outside_target_tolerance() -> None:
    origin, actual = backtest_pair()
    far_actual = NowcastSnapshot(
        hotspot_id=actual.hotspot_id,
        observed_at=actual.observed_at + timedelta(minutes=11),
        fetched_at=actual.fetched_at,
        level=actual.level,
        population_min=actual.population_min,
        population_max=actual.population_max,
        forecast_json=(),
    )

    report = backtest_nowcasts((origin, far_actual))

    assert report.samples_evaluated == 0
    assert dict(report.skip_counts)["actual_outside_tolerance"] == 1
    assert report.promotion_eligible is False


def test_lag_bucket_boundaries_are_inclusive_and_input_order_independent() -> None:
    observed = datetime(2026, 7, 11, 7, 0, tzinfo=UTC)
    snapshots: list[NowcastSnapshot] = []
    for hotspot_id, lag_min in enumerate((15, 16, 30, 31, 60, 61, 120, 121), 1):
        target = observed + timedelta(minutes=lag_min)
        snapshots.extend(
            (
                snapshot(
                    hotspot_id=hotspot_id,
                    observed_at=observed,
                    fetched_at=target,
                    level=1,
                    population_min=100,
                    population_max=100,
                    forecasts=(
                        forecast(
                            target.astimezone(ZoneInfo("Asia/Seoul")).strftime(
                                "%Y-%m-%d %H:%M"
                            ),
                            label="보통",
                            population_min=200,
                            population_max=200,
                        ),
                    ),
                ),
                snapshot(
                    hotspot_id=hotspot_id,
                    observed_at=target,
                    fetched_at=target + timedelta(minutes=30),
                    level=2,
                    population_min=200,
                    population_max=200,
                ),
            )
        )

    forward = backtest_nowcasts(snapshots)
    reverse = backtest_nowcasts(tuple(reversed(snapshots)))

    assert forward.lag_buckets == reverse.lag_buckets
    assert [bucket.samples for bucket in forward.lag_buckets] == [1, 2, 2, 2, 1]
    for bucket in forward.lag_buckets:
        assert bucket.nowcast_population_mae == pytest.approx(0)
        assert bucket.baseline_population_mae == pytest.approx(100)
        assert bucket.population_mae_delta == pytest.approx(-100)
        assert bucket.nowcast_level_exact_accuracy == pytest.approx(1)
        assert bucket.baseline_level_exact_accuracy == pytest.approx(0)


def test_database_loader_bounds_append_only_history() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    latest = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)
    with Session(engine) as session:
        hotspot = Hotspot(
            area_cd="POI001",
            name="fixture",
            lat=37.5,
            lng=127.0,
            is_polled=True,
        )
        session.add(hotspot)
        session.flush()
        for index, observed_at in enumerate(
            (latest - timedelta(days=15), latest), start=1
        ):
            session.add(
                HotspotSnapshot(
                    id=index,
                    hotspot_id=hotspot.id,
                    observed_at=observed_at,
                    fetched_at=observed_at + timedelta(minutes=30),
                    congest_level=2,
                    congest_label="보통",
                    ppltn_min=100,
                    ppltn_max=200,
                    forecast_json=[],
                )
            )
        session.commit()

        loaded = load_snapshots(session, window_days=14)

    engine.dispose()
    assert len(loaded) == 1
    assert loaded[0].observed_at.replace(tzinfo=UTC) == latest
