from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.scoring.source_delay_shadow import (
    SourceDelaySnapshot,
    backtest_source_delay,
)


def _snapshot(
    hotspot_id: int,
    minute: int,
    population: int,
    level: int,
) -> SourceDelaySnapshot:
    return SourceDelaySnapshot(
        hotspot_id=hotspot_id,
        observed_at=datetime(2026, 7, 1, 0, tzinfo=UTC) + timedelta(minutes=minute),
        level=level,
        population_min=population,
        population_max=population,
    )


def test_fixed_horizon_metrics_and_rank_are_deterministic() -> None:
    snapshots = (
        _snapshot(1, 0, 100, 1),
        _snapshot(1, 30, 200, 2),
        _snapshot(1, 60, 300, 3),
        _snapshot(1, 90, 400, 4),
        _snapshot(2, 0, 400, 4),
        _snapshot(2, 30, 300, 3),
        _snapshot(2, 60, 200, 2),
        _snapshot(2, 90, 100, 1),
    )

    forward = backtest_source_delay(
        snapshots,
        horizons_min=(30,),
        actual_tolerance_min=0,
    )
    reverse = backtest_source_delay(
        tuple(reversed(snapshots)),
        horizons_min=(30,),
        actual_tolerance_min=0,
    )

    assert forward == reverse
    horizon = forward.horizons[0]
    assert horizon.samples == 6
    assert horizon.population_mae == pytest.approx(100)
    assert horizon.population_wape == pytest.approx(0.4)
    assert horizon.population_bias == pytest.approx(0)
    assert horizon.level_exact_accuracy == pytest.approx(0)
    assert horizon.level_adjacent_accuracy == pytest.approx(1)
    assert horizon.population_temporal_hotspots == 2
    assert horizon.population_temporal_spearman_median == pytest.approx(1)
    assert horizon.level_spearman_median == pytest.approx(1)


def test_actual_tolerance_is_bounded_and_missing_is_reported() -> None:
    snapshots = (
        _snapshot(1, 0, 100, 1),
        _snapshot(1, 32, 200, 2),
    )

    included = backtest_source_delay(
        snapshots,
        horizons_min=(30,),
        actual_tolerance_min=2,
    )
    excluded = backtest_source_delay(
        snapshots,
        horizons_min=(30,),
        actual_tolerance_min=1.9,
    )

    assert included.horizons[0].samples == 1
    assert excluded.horizons[0].samples == 0
    assert excluded.horizons[0].missing_actual == 2


def test_invalid_or_duplicate_snapshots_fail_closed() -> None:
    valid = _snapshot(1, 0, 100, 1)
    naive = SourceDelaySnapshot(
        hotspot_id=1,
        observed_at=valid.observed_at.replace(tzinfo=None),
        level=1,
        population_min=100,
        population_max=100,
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        backtest_source_delay((naive,))
    with pytest.raises(ValueError, match="duplicate"):
        backtest_source_delay((valid, valid))
    with pytest.raises(ValueError, match="strictly increasing"):
        backtest_source_delay((valid,), horizons_min=(30, 30))
    with pytest.raises(ValueError, match="minimum_temporal_samples"):
        backtest_source_delay((valid,), minimum_temporal_samples=2)
