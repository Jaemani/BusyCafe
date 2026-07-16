"""Deterministic fixed-horizon persistence backtest for Seoul observations.

This same-source, offline shadow asks how well an observation at ``t``
reproduces a later snapshot at ``t+h``. It is neither cafe accuracy nor
independent ground truth and is never imported by the public scorer.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import fsum, isfinite, sqrt
from statistics import median

from app.config import (
    SOURCE_DELAY_SHADOW_ACTUAL_TOLERANCE_MIN,
    SOURCE_DELAY_SHADOW_HORIZONS_MIN,
    SOURCE_DELAY_SHADOW_MIN_SLOT_HOTSPOTS,
    SOURCE_DELAY_SHADOW_MIN_TEMPORAL_SAMPLES,
    SOURCE_DELAY_SHADOW_MODEL_VERSION,
)


@dataclass(frozen=True, slots=True)
class SourceDelaySnapshot:
    hotspot_id: int
    observed_at: datetime
    level: int
    population_min: int
    population_max: int


@dataclass(frozen=True, slots=True)
class SourceDelayHorizonReport:
    horizon_min: int
    origins: int
    samples: int
    missing_actual: int
    hotspots: int
    target_slots: int
    population_mae: float | None
    population_wape: float | None
    population_bias: float | None
    level_exact_accuracy: float | None
    level_adjacent_accuracy: float | None
    population_temporal_hotspots: int
    population_temporal_spearman_median: float | None
    population_temporal_spearman_min: float | None
    level_rank_slots: int
    level_spearman_median: float | None
    level_spearman_min: float | None


@dataclass(frozen=True, slots=True)
class SourceDelayBacktestReport:
    model_version: str
    claim: str
    public_model_effect: str
    snapshots: int
    hotspots: int
    span_hours: float
    actual_tolerance_min: float
    horizons: tuple[SourceDelayHorizonReport, ...]


@dataclass(frozen=True, slots=True)
class _Pair:
    hotspot_id: int
    target_at: datetime
    origin_population: float
    actual_population: float
    origin_level: int
    actual_level: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(UTC)


def _normalize(
    snapshots: Sequence[SourceDelaySnapshot],
) -> tuple[SourceDelaySnapshot, ...]:
    normalized: list[SourceDelaySnapshot] = []
    seen: set[tuple[int, datetime]] = set()
    for item in snapshots:
        if item.hotspot_id <= 0:
            raise ValueError("hotspot_id must be positive")
        observed_at = _utc(item.observed_at)
        if item.level not in (1, 2, 3, 4):
            raise ValueError("level must be between 1 and 4")
        if item.population_min < 0 or item.population_max < item.population_min:
            raise ValueError("population bounds must satisfy 0 <= min <= max")
        key = (item.hotspot_id, observed_at)
        if key in seen:
            raise ValueError("duplicate hotspot observation")
        seen.add(key)
        normalized.append(
            SourceDelaySnapshot(
                hotspot_id=item.hotspot_id,
                observed_at=observed_at,
                level=item.level,
                population_min=item.population_min,
                population_max=item.population_max,
            )
        )
    return tuple(
        sorted(normalized, key=lambda item: (item.hotspot_id, item.observed_at))
    )


def _midpoint(item: SourceDelaySnapshot) -> float:
    return (item.population_min + item.population_max) / 2.0


def _average_ranks(values: Sequence[float]) -> tuple[float, ...]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[ordered[position][0]] = average
        start = end
    return tuple(ranks)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = fsum(left_ranks) / len(left_ranks)
    right_mean = fsum(right_ranks) / len(right_ranks)
    numerator = fsum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_ranks, right_ranks, strict=True)
    )
    left_norm = sqrt(fsum((value - left_mean) ** 2 for value in left_ranks))
    right_norm = sqrt(fsum((value - right_mean) ** 2 for value in right_ranks))
    if left_norm == 0 or right_norm == 0:
        return None
    return numerator / (left_norm * right_norm)


def _level_rank_summary(
    pairs: Sequence[_Pair],
    *,
    minimum_hotspots: int,
) -> tuple[int, float | None, float | None]:
    grouped: dict[datetime, list[_Pair]] = defaultdict(list)
    for pair in pairs:
        grouped[pair.target_at].append(pair)
    values: list[float] = []
    for target_at in sorted(grouped):
        items = sorted(grouped[target_at], key=lambda item: item.hotspot_id)
        if len(items) < minimum_hotspots:
            continue
        left = [float(item.origin_level) for item in items]
        right = [float(item.actual_level) for item in items]
        value = _spearman(left, right)
        if value is not None:
            values.append(value)
    if not values:
        return 0, None, None
    return len(values), float(median(values)), min(values)


def _population_temporal_rank_summary(
    pairs: Sequence[_Pair],
    *,
    minimum_samples: int,
) -> tuple[int, float | None, float | None]:
    """Compare population movement only within each source hotspot over time."""

    grouped: dict[int, list[_Pair]] = defaultdict(list)
    for pair in pairs:
        grouped[pair.hotspot_id].append(pair)
    values: list[float] = []
    for hotspot_id in sorted(grouped):
        items = sorted(
            grouped[hotspot_id],
            key=lambda item: (item.target_at, item.origin_population),
        )
        if len(items) < minimum_samples:
            continue
        value = _spearman(
            [item.origin_population for item in items],
            [item.actual_population for item in items],
        )
        if value is not None:
            values.append(value)
    if not values:
        return 0, None, None
    return len(values), float(median(values)), min(values)


def _nearest_actual(
    items: Sequence[SourceDelaySnapshot],
    times: Sequence[datetime],
    target_at: datetime,
    tolerance: timedelta,
) -> SourceDelaySnapshot | None:
    index = bisect_left(times, target_at)
    candidates = items[max(0, index - 1) : min(len(items), index + 2)]
    if not candidates:
        return None
    selected = min(
        candidates,
        key=lambda item: (abs(item.observed_at - target_at), item.observed_at),
    )
    return selected if abs(selected.observed_at - target_at) <= tolerance else None


def backtest_source_delay(
    snapshots: Sequence[SourceDelaySnapshot],
    *,
    horizons_min: Sequence[int] = SOURCE_DELAY_SHADOW_HORIZONS_MIN,
    actual_tolerance_min: float = SOURCE_DELAY_SHADOW_ACTUAL_TOLERANCE_MIN,
    minimum_slot_hotspots: int = SOURCE_DELAY_SHADOW_MIN_SLOT_HOTSPOTS,
    minimum_temporal_samples: int = SOURCE_DELAY_SHADOW_MIN_TEMPORAL_SAMPLES,
) -> SourceDelayBacktestReport:
    """Evaluate a no-change baseline at fixed future horizons."""

    horizons = tuple(int(value) for value in horizons_min)
    if (
        not horizons
        or any(value <= 0 for value in horizons)
        or any(current <= previous for previous, current in zip(horizons, horizons[1:]))
    ):
        raise ValueError("horizons must be positive and strictly increasing")
    if not isfinite(actual_tolerance_min) or actual_tolerance_min < 0:
        raise ValueError("actual_tolerance_min must be finite and non-negative")
    if minimum_slot_hotspots < 2:
        raise ValueError("minimum_slot_hotspots must be at least two")
    if minimum_temporal_samples < 3:
        raise ValueError("minimum_temporal_samples must be at least three")

    normalized = _normalize(snapshots)
    by_hotspot: dict[int, list[SourceDelaySnapshot]] = defaultdict(list)
    for item in normalized:
        by_hotspot[item.hotspot_id].append(item)
    times_by_hotspot = {
        hotspot_id: tuple(item.observed_at for item in items)
        for hotspot_id, items in by_hotspot.items()
    }
    tolerance = timedelta(minutes=actual_tolerance_min)
    horizon_reports: list[SourceDelayHorizonReport] = []
    for horizon in horizons:
        pairs: list[_Pair] = []
        for origin in normalized:
            target_at = origin.observed_at + timedelta(minutes=horizon)
            actual = _nearest_actual(
                by_hotspot[origin.hotspot_id],
                times_by_hotspot[origin.hotspot_id],
                target_at,
                tolerance,
            )
            if actual is None or actual.observed_at <= origin.observed_at:
                continue
            pairs.append(
                _Pair(
                    hotspot_id=origin.hotspot_id,
                    target_at=actual.observed_at,
                    origin_population=_midpoint(origin),
                    actual_population=_midpoint(actual),
                    origin_level=origin.level,
                    actual_level=actual.level,
                )
            )
        absolute_errors = [
            abs(item.origin_population - item.actual_population) for item in pairs
        ]
        actual_total = fsum(item.actual_population for item in pairs)
        population_hotspots, population_median, population_minimum = (
            _population_temporal_rank_summary(
                pairs,
                minimum_samples=minimum_temporal_samples,
            )
        )
        level_slots, level_median, level_minimum = _level_rank_summary(
            pairs,
            minimum_hotspots=minimum_slot_hotspots,
        )
        horizon_reports.append(
            SourceDelayHorizonReport(
                horizon_min=horizon,
                origins=len(normalized),
                samples=len(pairs),
                missing_actual=len(normalized) - len(pairs),
                hotspots=len({item.hotspot_id for item in pairs}),
                target_slots=len({item.target_at for item in pairs}),
                population_mae=(fsum(absolute_errors) / len(pairs) if pairs else None),
                population_wape=(
                    fsum(absolute_errors) / actual_total if actual_total else None
                ),
                population_bias=(
                    fsum(
                        item.origin_population - item.actual_population
                        for item in pairs
                    )
                    / len(pairs)
                    if pairs
                    else None
                ),
                level_exact_accuracy=(
                    fsum(item.origin_level == item.actual_level for item in pairs)
                    / len(pairs)
                    if pairs
                    else None
                ),
                level_adjacent_accuracy=(
                    fsum(
                        abs(item.origin_level - item.actual_level) <= 1
                        for item in pairs
                    )
                    / len(pairs)
                    if pairs
                    else None
                ),
                population_temporal_hotspots=population_hotspots,
                population_temporal_spearman_median=population_median,
                population_temporal_spearman_min=population_minimum,
                level_rank_slots=level_slots,
                level_spearman_median=level_median,
                level_spearman_min=level_minimum,
            )
        )

    observed_times = [item.observed_at for item in normalized]
    span_hours = (
        (max(observed_times) - min(observed_times)).total_seconds() / 3_600
        if len(observed_times) >= 2
        else 0.0
    )
    return SourceDelayBacktestReport(
        model_version=SOURCE_DELAY_SHADOW_MODEL_VERSION,
        claim="same-source fixed-horizon persistence; not cafe accuracy",
        public_model_effect="none; offline shadow only",
        snapshots=len(normalized),
        hotspots=len(by_hotspot),
        span_hours=span_hours,
        actual_tolerance_min=actual_tolerance_min,
        horizons=tuple(horizon_reports),
    )
