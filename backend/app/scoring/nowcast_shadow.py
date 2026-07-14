"""Deterministic offline backtest for correcting Seoul observation lag.

Public v1 never imports this module.  A saved forecast is interpolated to the
time at which its source snapshot was fetched, then compared with an actual
snapshot that became available later.  This tests upstream forecast usefulness;
it is not cafe-level accuracy evidence.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import floor, isfinite
from typing import Literal
from zoneinfo import ZoneInfo

from app.config import (
    CONGESTION_LEVELS,
    NOWCAST_HYBRID_SHADOW_MODEL_VERSION,
    NOWCAST_HYBRID_TIE_ABS_EPSILON,
    NOWCAST_SHADOW_ACTUAL_TOLERANCE_MIN,
    NOWCAST_SHADOW_LAG_BUCKET_EDGES_MIN,
    NOWCAST_SHADOW_MAX_HORIZON_MIN,
    NOWCAST_SHADOW_MAX_INTERPOLATION_GAP_MIN,
    NOWCAST_SHADOW_MAX_POPULATION_WAPE,
    NOWCAST_SHADOW_MIN_HOTSPOTS,
    NOWCAST_SHADOW_MIN_LEVEL_ADJACENT_ACCURACY,
    NOWCAST_SHADOW_MIN_LEVEL_EXACT_ACCURACY,
    NOWCAST_SHADOW_MIN_SAMPLES,
    NOWCAST_SHADOW_MIN_SPAN_DAYS,
    NOWCAST_SHADOW_MODEL_VERSION,
    NOWCAST_SHADOW_NEAREST_TOLERANCE_MIN,
    NOWCAST_SHADOW_TIME_FORMAT,
)


SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
SelectionMethod = Literal["exact", "interpolated", "nearest"]
ComparisonOutcome = Literal["win", "tie", "loss"]


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    at: datetime
    level: int
    population_min: int
    population_max: int


@dataclass(frozen=True, slots=True)
class NowcastSnapshot:
    hotspot_id: int
    observed_at: datetime
    fetched_at: datetime
    level: int
    population_min: int
    population_max: int
    forecast_json: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class NowcastEstimate:
    target_at: datetime
    level_score: float
    level: int
    population_min: float
    population_max: float
    method: SelectionMethod
    source_before_at: datetime | None
    source_after_at: datetime


@dataclass(frozen=True, slots=True)
class LagBucketDiagnostics:
    label: str
    lower_bound_exclusive_min: float | None
    upper_bound_inclusive_min: float | None
    samples: int
    mean_lag_min: float | None
    nowcast_population_mae: float | None
    baseline_population_mae: float | None
    population_mae_delta: float | None
    nowcast_population_wape: float | None
    baseline_population_wape: float | None
    nowcast_level_exact_accuracy: float | None
    baseline_level_exact_accuracy: float | None
    level_exact_accuracy_delta: float | None
    nowcast_level_adjacent_accuracy: float | None
    baseline_level_adjacent_accuracy: float | None
    level_adjacent_accuracy_delta: float | None


@dataclass(frozen=True, slots=True)
class ComparisonOutcomes:
    eligible_groups: int
    wins: int
    ties: int
    losses: int
    win_rate: float | None


@dataclass(frozen=True, slots=True)
class HotspotDayPopulationDiagnostics:
    hotspot_id: int
    local_day: str
    samples: int
    baseline_population_mae: float
    hybrid_population_mae: float
    population_mae_delta: float
    baseline_population_wape: float | None
    hybrid_population_wape: float | None
    population_wape_delta: float | None
    mae_outcome: ComparisonOutcome
    wape_outcome: ComparisonOutcome | None


@dataclass(frozen=True, slots=True)
class PopulationStratumDiagnostics:
    stratum_key: str
    hotspot_day_groups: int
    samples: int
    baseline_population_mae: float | None
    hybrid_population_mae: float | None
    population_mae_delta: float | None
    baseline_population_wape: float | None
    hybrid_population_wape: float | None
    population_wape_delta: float | None
    mae_outcomes: ComparisonOutcomes
    wape_outcomes: ComparisonOutcomes


@dataclass(frozen=True, slots=True)
class HybridComparatorReport:
    model_version: str
    population_policy: str
    level_policy: str
    population_mae: float | None
    population_wape: float | None
    population_bias: float | None
    level_exact_accuracy: float | None
    level_adjacent_accuracy: float | None
    level_exact_accuracy_delta: float | None
    level_adjacent_accuracy_delta: float | None
    hotspot_day: tuple[HotspotDayPopulationDiagnostics, ...]
    aggregate: PopulationStratumDiagnostics
    by_hotspot: tuple[PopulationStratumDiagnostics, ...]
    by_day: tuple[PopulationStratumDiagnostics, ...]


@dataclass(frozen=True, slots=True)
class NowcastBacktestReport:
    model_version: str
    origins_total: int
    samples_evaluated: int
    hotspots_evaluated: int
    span_days: float
    skip_counts: tuple[tuple[str, int], ...]
    mean_observation_lag_min: float | None
    lag_buckets: tuple[LagBucketDiagnostics, ...]
    nowcast_population_mae: float | None
    baseline_population_mae: float | None
    nowcast_population_wape: float | None
    baseline_population_wape: float | None
    nowcast_population_bias: float | None
    nowcast_level_exact_accuracy: float | None
    baseline_level_exact_accuracy: float | None
    nowcast_level_adjacent_accuracy: float | None
    baseline_level_adjacent_accuracy: float | None
    hybrid: HybridComparatorReport
    sample_sufficient: bool
    quality_passed: bool
    promotion_eligible: bool
    promotion_blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _MetricSample:
    hotspot_id: int
    target_at: datetime
    lag_min: float
    actual_midpoint: float
    nowcast_population_error: float
    baseline_population_error: float
    nowcast_population_bias: float
    nowcast_level_exact: float
    baseline_level_exact: float
    nowcast_level_adjacent: float
    baseline_level_adjacent: float


@dataclass(frozen=True, slots=True)
class _MetricSummary:
    samples: int
    mean_lag_min: float | None
    nowcast_population_mae: float | None
    baseline_population_mae: float | None
    nowcast_population_wape: float | None
    baseline_population_wape: float | None
    nowcast_population_bias: float | None
    nowcast_level_exact_accuracy: float | None
    baseline_level_exact_accuracy: float | None
    nowcast_level_adjacent_accuracy: float | None
    baseline_level_adjacent_accuracy: float | None


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_level(level: int) -> None:
    if level not in (1, 2, 3, 4):
        raise ValueError("level must be between 1 and 4")


def _validate_population(minimum: int, maximum: int) -> None:
    if minimum < 0 or maximum < minimum:
        raise ValueError("population bounds must satisfy 0 <= min <= max")


def _half_up(value: float) -> int:
    return min(4, max(1, floor(value + 0.5)))


def parse_forecast_points(
    raw_forecast: Sequence[Mapping[str, object]],
) -> tuple[ForecastPoint, ...]:
    """Strictly parse the already-validated raw Seoul forecast payload."""

    by_time: dict[datetime, ForecastPoint] = {}
    for item in raw_forecast:
        try:
            raw_time = item["FCST_TIME"]
            raw_label = item["FCST_CONGEST_LVL"]
            if not isinstance(raw_time, str) or not isinstance(raw_label, str):
                raise ValueError("forecast time and label must be strings")
            at = (
                datetime.strptime(raw_time, NOWCAST_SHADOW_TIME_FORMAT)
                .replace(tzinfo=SEOUL_TIMEZONE)
                .astimezone(UTC)
            )
            level = CONGESTION_LEVELS[raw_label]
            raw_population_min = item["FCST_PPLTN_MIN"]
            raw_population_max = item["FCST_PPLTN_MAX"]
            if (
                isinstance(raw_population_min, bool)
                or not isinstance(raw_population_min, (str, int))
                or isinstance(raw_population_max, bool)
                or not isinstance(raw_population_max, (str, int))
            ):
                raise ValueError("forecast population bounds must be integers")
            population_min = int(raw_population_min)
            population_max = int(raw_population_max)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid forecast point") from error
        _validate_population(population_min, population_max)
        point = ForecastPoint(
            at=at,
            level=level,
            population_min=population_min,
            population_max=population_max,
        )
        previous = by_time.get(at)
        if previous is not None and previous != point:
            raise ValueError("conflicting duplicate forecast time")
        by_time[at] = point
    return tuple(by_time[at] for at in sorted(by_time))


def estimate_nowcast(
    snapshot: NowcastSnapshot,
    *,
    target_at: datetime | None = None,
    max_horizon_min: float = NOWCAST_SHADOW_MAX_HORIZON_MIN,
    max_interpolation_gap_min: float = NOWCAST_SHADOW_MAX_INTERPOLATION_GAP_MIN,
    nearest_tolerance_min: float = NOWCAST_SHADOW_NEAREST_TOLERANCE_MIN,
) -> NowcastEstimate | None:
    """Interpolate a saved forecast curve to a historical target time."""

    if max_horizon_min <= 0 or max_interpolation_gap_min <= 0:
        raise ValueError("nowcast horizon and interpolation gap must be positive")
    if nearest_tolerance_min < 0:
        raise ValueError("nearest tolerance must be nonnegative")
    observed_at = _utc(snapshot.observed_at, field="observed_at")
    target = _utc(target_at or snapshot.fetched_at, field="target_at")
    _utc(snapshot.fetched_at, field="fetched_at")
    _validate_level(snapshot.level)
    _validate_population(snapshot.population_min, snapshot.population_max)
    horizon_min = (target - observed_at).total_seconds() / 60.0
    if horizon_min < 0 or horizon_min > max_horizon_min:
        return None

    forecasts = tuple(
        point
        for point in parse_forecast_points(snapshot.forecast_json)
        if observed_at < point.at
        and (point.at - observed_at).total_seconds() / 60.0 <= max_horizon_min
    )
    if not forecasts:
        return None

    for point in forecasts:
        if point.at == target:
            return NowcastEstimate(
                target_at=target,
                level_score=float(point.level),
                level=point.level,
                population_min=float(point.population_min),
                population_max=float(point.population_max),
                method="exact",
                source_before_at=None,
                source_after_at=point.at,
            )

    anchors = (
        ForecastPoint(
            at=observed_at,
            level=snapshot.level,
            population_min=snapshot.population_min,
            population_max=snapshot.population_max,
        ),
        *forecasts,
    )
    for before, after in zip(anchors, anchors[1:]):
        if before.at < target < after.at:
            gap_min = (after.at - before.at).total_seconds() / 60.0
            if gap_min <= max_interpolation_gap_min:
                fraction = (target - before.at).total_seconds() / (
                    after.at - before.at
                ).total_seconds()
                level_score = before.level + fraction * (after.level - before.level)
                population_min = before.population_min + fraction * (
                    after.population_min - before.population_min
                )
                population_max = before.population_max + fraction * (
                    after.population_max - before.population_max
                )
                return NowcastEstimate(
                    target_at=target,
                    level_score=level_score,
                    level=_half_up(level_score),
                    population_min=population_min,
                    population_max=population_max,
                    method="interpolated",
                    source_before_at=before.at,
                    source_after_at=after.at,
                )
            break

    nearest = min(
        forecasts,
        key=lambda point: (abs((point.at - target).total_seconds()), point.at),
    )
    distance_min = abs((nearest.at - target).total_seconds()) / 60.0
    if distance_min > nearest_tolerance_min:
        return None
    return NowcastEstimate(
        target_at=target,
        level_score=float(nearest.level),
        level=nearest.level,
        population_min=float(nearest.population_min),
        population_max=float(nearest.population_max),
        method="nearest",
        source_before_at=None,
        source_after_at=nearest.at,
    )


def _midpoint(minimum: float, maximum: float) -> float:
    return (minimum + maximum) / 2.0


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _summarize_metrics(samples: Sequence[_MetricSample]) -> _MetricSummary:
    actual_population = sum(item.actual_midpoint for item in samples)
    nowcast_errors = [item.nowcast_population_error for item in samples]
    baseline_errors = [item.baseline_population_error for item in samples]
    return _MetricSummary(
        samples=len(samples),
        mean_lag_min=_mean([item.lag_min for item in samples]),
        nowcast_population_mae=_mean(nowcast_errors),
        baseline_population_mae=_mean(baseline_errors),
        nowcast_population_wape=(
            sum(nowcast_errors) / actual_population
            if actual_population > 0
            else None
        ),
        baseline_population_wape=(
            sum(baseline_errors) / actual_population
            if actual_population > 0
            else None
        ),
        nowcast_population_bias=_mean(
            [item.nowcast_population_bias for item in samples]
        ),
        nowcast_level_exact_accuracy=_mean(
            [item.nowcast_level_exact for item in samples]
        ),
        baseline_level_exact_accuracy=_mean(
            [item.baseline_level_exact for item in samples]
        ),
        nowcast_level_adjacent_accuracy=_mean(
            [item.nowcast_level_adjacent for item in samples]
        ),
        baseline_level_adjacent_accuracy=_mean(
            [item.baseline_level_adjacent for item in samples]
        ),
    )


def _delta(challenger: float | None, baseline: float | None) -> float | None:
    if challenger is None or baseline is None:
        return None
    return challenger - baseline


def _format_lag_edge(value: float) -> str:
    return f"{value:g}"


def _build_lag_bucket_diagnostics(
    samples: Sequence[_MetricSample],
    edges_min: Sequence[float],
) -> tuple[LagBucketDiagnostics, ...]:
    edges = tuple(float(value) for value in edges_min)
    if not edges or not all(isfinite(value) for value in edges) or edges[0] <= 0 or any(
        current <= previous for previous, current in zip(edges, edges[1:])
    ):
        raise ValueError("lag bucket edges must be positive and strictly increasing")
    bucket_samples: list[list[_MetricSample]] = [list() for _ in range(len(edges) + 1)]
    for sample in samples:
        bucket_index = next(
            (index for index, edge in enumerate(edges) if sample.lag_min <= edge),
            len(edges),
        )
        bucket_samples[bucket_index].append(sample)

    diagnostics: list[LagBucketDiagnostics] = []
    for index, items in enumerate(bucket_samples):
        lower = edges[index - 1] if index > 0 else None
        upper = edges[index] if index < len(edges) else None
        if lower is None and upper is not None:
            label = f"<={_format_lag_edge(upper)}"
        elif upper is None and lower is not None:
            label = f">{_format_lag_edge(lower)}"
        else:
            if lower is None or upper is None:  # impossible with non-empty edges
                raise AssertionError("lag bucket bounds are inconsistent")
            label = f"{_format_lag_edge(lower)}-{_format_lag_edge(upper)}"
        summary = _summarize_metrics(items)
        diagnostics.append(
            LagBucketDiagnostics(
                label=label,
                lower_bound_exclusive_min=lower,
                upper_bound_inclusive_min=upper,
                samples=summary.samples,
                mean_lag_min=summary.mean_lag_min,
                nowcast_population_mae=summary.nowcast_population_mae,
                baseline_population_mae=summary.baseline_population_mae,
                population_mae_delta=_delta(
                    summary.nowcast_population_mae,
                    summary.baseline_population_mae,
                ),
                nowcast_population_wape=summary.nowcast_population_wape,
                baseline_population_wape=summary.baseline_population_wape,
                nowcast_level_exact_accuracy=(
                    summary.nowcast_level_exact_accuracy
                ),
                baseline_level_exact_accuracy=(
                    summary.baseline_level_exact_accuracy
                ),
                level_exact_accuracy_delta=_delta(
                    summary.nowcast_level_exact_accuracy,
                    summary.baseline_level_exact_accuracy,
                ),
                nowcast_level_adjacent_accuracy=(
                    summary.nowcast_level_adjacent_accuracy
                ),
                baseline_level_adjacent_accuracy=(
                    summary.baseline_level_adjacent_accuracy
                ),
                level_adjacent_accuracy_delta=_delta(
                    summary.nowcast_level_adjacent_accuracy,
                    summary.baseline_level_adjacent_accuracy,
                ),
            )
        )
    return tuple(diagnostics)


def _comparison_outcome(delta: float) -> ComparisonOutcome:
    if delta < -NOWCAST_HYBRID_TIE_ABS_EPSILON:
        return "win"
    if delta > NOWCAST_HYBRID_TIE_ABS_EPSILON:
        return "loss"
    return "tie"


def _outcome_counts(
    outcomes: Sequence[ComparisonOutcome | None],
) -> ComparisonOutcomes:
    eligible = [outcome for outcome in outcomes if outcome is not None]
    wins = eligible.count("win")
    return ComparisonOutcomes(
        eligible_groups=len(eligible),
        wins=wins,
        ties=eligible.count("tie"),
        losses=eligible.count("loss"),
        win_rate=wins / len(eligible) if eligible else None,
    )


def _build_hotspot_day_diagnostics(
    samples: Sequence[_MetricSample],
) -> tuple[HotspotDayPopulationDiagnostics, ...]:
    grouped: dict[tuple[int, str], list[_MetricSample]] = defaultdict(list)
    for sample in samples:
        local_day = sample.target_at.astimezone(SEOUL_TIMEZONE).date().isoformat()
        grouped[(sample.hotspot_id, local_day)].append(sample)

    diagnostics: list[HotspotDayPopulationDiagnostics] = []
    for (hotspot_id, local_day), items in sorted(grouped.items()):
        summary = _summarize_metrics(items)
        if (
            summary.baseline_population_mae is None
            or summary.nowcast_population_mae is None
        ):
            raise AssertionError("non-empty hotspot/day group has no MAE")
        mae_delta = (
            summary.nowcast_population_mae - summary.baseline_population_mae
        )
        wape_delta = _delta(
            summary.nowcast_population_wape,
            summary.baseline_population_wape,
        )
        diagnostics.append(
            HotspotDayPopulationDiagnostics(
                hotspot_id=hotspot_id,
                local_day=local_day,
                samples=len(items),
                baseline_population_mae=summary.baseline_population_mae,
                hybrid_population_mae=summary.nowcast_population_mae,
                population_mae_delta=mae_delta,
                baseline_population_wape=summary.baseline_population_wape,
                hybrid_population_wape=summary.nowcast_population_wape,
                population_wape_delta=wape_delta,
                mae_outcome=_comparison_outcome(mae_delta),
                wape_outcome=(
                    _comparison_outcome(wape_delta)
                    if wape_delta is not None
                    else None
                ),
            )
        )
    return tuple(diagnostics)


def _build_population_stratum(
    key: str,
    samples: Sequence[_MetricSample],
    groups: Sequence[HotspotDayPopulationDiagnostics],
) -> PopulationStratumDiagnostics:
    summary = _summarize_metrics(samples)
    return PopulationStratumDiagnostics(
        stratum_key=key,
        hotspot_day_groups=len(groups),
        samples=len(samples),
        baseline_population_mae=summary.baseline_population_mae,
        hybrid_population_mae=summary.nowcast_population_mae,
        population_mae_delta=_delta(
            summary.nowcast_population_mae,
            summary.baseline_population_mae,
        ),
        baseline_population_wape=summary.baseline_population_wape,
        hybrid_population_wape=summary.nowcast_population_wape,
        population_wape_delta=_delta(
            summary.nowcast_population_wape,
            summary.baseline_population_wape,
        ),
        mae_outcomes=_outcome_counts([group.mae_outcome for group in groups]),
        wape_outcomes=_outcome_counts([group.wape_outcome for group in groups]),
    )


def _build_hybrid_comparator(
    samples: Sequence[_MetricSample],
    summary: _MetricSummary,
) -> HybridComparatorReport:
    hotspot_day = _build_hotspot_day_diagnostics(samples)
    samples_by_hotspot: dict[int, list[_MetricSample]] = defaultdict(list)
    samples_by_day: dict[str, list[_MetricSample]] = defaultdict(list)
    groups_by_hotspot: dict[int, list[HotspotDayPopulationDiagnostics]] = (
        defaultdict(list)
    )
    groups_by_day: dict[str, list[HotspotDayPopulationDiagnostics]] = defaultdict(
        list
    )
    for sample in samples:
        local_day = sample.target_at.astimezone(SEOUL_TIMEZONE).date().isoformat()
        samples_by_hotspot[sample.hotspot_id].append(sample)
        samples_by_day[local_day].append(sample)
    for group in hotspot_day:
        groups_by_hotspot[group.hotspot_id].append(group)
        groups_by_day[group.local_day].append(group)

    return HybridComparatorReport(
        model_version=NOWCAST_HYBRID_SHADOW_MODEL_VERSION,
        population_policy="forecast_interpolated_or_nearest",
        level_policy="latest_observed",
        population_mae=summary.nowcast_population_mae,
        population_wape=summary.nowcast_population_wape,
        population_bias=summary.nowcast_population_bias,
        # Hybrid deliberately retains the observed ordinal label, so its label
        # metrics must equal the delayed baseline rather than forecast labels.
        level_exact_accuracy=summary.baseline_level_exact_accuracy,
        level_adjacent_accuracy=summary.baseline_level_adjacent_accuracy,
        level_exact_accuracy_delta=_delta(
            summary.baseline_level_exact_accuracy,
            summary.baseline_level_exact_accuracy,
        ),
        level_adjacent_accuracy_delta=_delta(
            summary.baseline_level_adjacent_accuracy,
            summary.baseline_level_adjacent_accuracy,
        ),
        hotspot_day=hotspot_day,
        aggregate=_build_population_stratum("all", samples, hotspot_day),
        by_hotspot=tuple(
            _build_population_stratum(
                str(hotspot_id),
                samples_by_hotspot[hotspot_id],
                groups_by_hotspot[hotspot_id],
            )
            for hotspot_id in sorted(samples_by_hotspot)
        ),
        by_day=tuple(
            _build_population_stratum(
                local_day,
                samples_by_day[local_day],
                groups_by_day[local_day],
            )
            for local_day in sorted(samples_by_day)
        ),
    )


def backtest_nowcasts(
    snapshots: Sequence[NowcastSnapshot],
    *,
    actual_tolerance_min: float = NOWCAST_SHADOW_ACTUAL_TOLERANCE_MIN,
    min_samples: int = NOWCAST_SHADOW_MIN_SAMPLES,
    min_hotspots: int = NOWCAST_SHADOW_MIN_HOTSPOTS,
    min_span_days: float = NOWCAST_SHADOW_MIN_SPAN_DAYS,
    max_population_wape: float = NOWCAST_SHADOW_MAX_POPULATION_WAPE,
    min_level_exact_accuracy: float = NOWCAST_SHADOW_MIN_LEVEL_EXACT_ACCURACY,
    min_level_adjacent_accuracy: float = (
        NOWCAST_SHADOW_MIN_LEVEL_ADJACENT_ACCURACY
    ),
    lag_bucket_edges_min: Sequence[float] = NOWCAST_SHADOW_LAG_BUCKET_EDGES_MIN,
) -> NowcastBacktestReport:
    """Compare historical fetch-time nowcasts with later-arriving actuals."""

    if actual_tolerance_min < 0:
        raise ValueError("actual tolerance must be nonnegative")
    if min_samples < 1 or min_hotspots < 1 or min_span_days < 0:
        raise ValueError("sample gates are invalid")
    if not 0 <= max_population_wape or not 0 <= min_level_exact_accuracy <= 1:
        raise ValueError("quality gates are invalid")
    if not 0 <= min_level_adjacent_accuracy <= 1:
        raise ValueError("quality gates are invalid")

    normalized: list[NowcastSnapshot] = []
    for snapshot in snapshots:
        _validate_level(snapshot.level)
        _validate_population(snapshot.population_min, snapshot.population_max)
        normalized.append(
            NowcastSnapshot(
                hotspot_id=snapshot.hotspot_id,
                observed_at=_utc(snapshot.observed_at, field="observed_at"),
                fetched_at=_utc(snapshot.fetched_at, field="fetched_at"),
                level=snapshot.level,
                population_min=snapshot.population_min,
                population_max=snapshot.population_max,
                forecast_json=snapshot.forecast_json,
            )
        )
    normalized.sort(
        key=lambda item: (item.hotspot_id, item.observed_at, item.fetched_at)
    )
    by_hotspot: dict[int, list[NowcastSnapshot]] = defaultdict(list)
    for snapshot in normalized:
        by_hotspot[snapshot.hotspot_id].append(snapshot)
    observed_times_by_hotspot = {
        hotspot_id: [item.observed_at for item in items]
        for hotspot_id, items in by_hotspot.items()
    }

    skip_counts: dict[str, int] = defaultdict(int)
    metric_samples: list[_MetricSample] = []

    for origin in normalized:
        if not origin.forecast_json:
            skip_counts["no_forecast"] += 1
            continue
        try:
            estimate = estimate_nowcast(origin)
        except ValueError:
            skip_counts["invalid_forecast"] += 1
            continue
        if estimate is None:
            skip_counts["no_target_estimate"] += 1
            continue

        hotspot_snapshots = by_hotspot[origin.hotspot_id]
        observed_times = observed_times_by_hotspot[origin.hotspot_id]
        tolerance = timedelta(minutes=actual_tolerance_min)
        start = bisect_left(observed_times, estimate.target_at - tolerance)
        end = bisect_right(observed_times, estimate.target_at + tolerance)
        later = [
            candidate
            for candidate in hotspot_snapshots[start:end]
            if candidate.fetched_at > origin.fetched_at
            and candidate.observed_at > origin.observed_at
        ]
        if not later:
            skip_counts["actual_outside_tolerance"] += 1
            continue
        actual = min(
            later,
            key=lambda item: (
                abs((item.observed_at - estimate.target_at).total_seconds()),
                item.observed_at,
                item.fetched_at,
            ),
        )
        actual_delta_min = abs(
            (actual.observed_at - estimate.target_at).total_seconds()
        ) / 60.0
        if actual_delta_min > actual_tolerance_min:  # defensive float boundary
            raise AssertionError("actual matching exceeded configured tolerance")

        actual_midpoint = _midpoint(actual.population_min, actual.population_max)
        nowcast_midpoint = _midpoint(
            estimate.population_min, estimate.population_max
        )
        baseline_midpoint = _midpoint(origin.population_min, origin.population_max)
        nowcast_error = abs(nowcast_midpoint - actual_midpoint)
        baseline_error = abs(baseline_midpoint - actual_midpoint)
        metric_samples.append(
            _MetricSample(
                hotspot_id=origin.hotspot_id,
                target_at=estimate.target_at,
                lag_min=(
                    (origin.fetched_at - origin.observed_at).total_seconds()
                    / 60.0
                ),
                actual_midpoint=actual_midpoint,
                nowcast_population_error=nowcast_error,
                baseline_population_error=baseline_error,
                nowcast_population_bias=nowcast_midpoint - actual_midpoint,
                nowcast_level_exact=float(estimate.level == actual.level),
                baseline_level_exact=float(origin.level == actual.level),
                nowcast_level_adjacent=float(
                    abs(estimate.level - actual.level) <= 1
                ),
                baseline_level_adjacent=float(abs(origin.level - actual.level) <= 1),
            )
        )

    samples = len(metric_samples)
    target_times = [item.target_at for item in metric_samples]
    evaluated_hotspots = {item.hotspot_id for item in metric_samples}
    span_days = (
        (max(target_times) - min(target_times)).total_seconds() / 86_400.0
        if len(target_times) >= 2
        else 0.0
    )
    summary = _summarize_metrics(metric_samples)
    lag_buckets = _build_lag_bucket_diagnostics(
        metric_samples, lag_bucket_edges_min
    )

    blockers: list[str] = []
    if samples < min_samples:
        blockers.append("insufficient_samples")
    if len(evaluated_hotspots) < min_hotspots:
        blockers.append("insufficient_hotspots")
    if span_days < min_span_days:
        blockers.append("insufficient_span")
    sample_sufficient = not blockers

    if (
        summary.nowcast_population_wape is None
        or summary.nowcast_population_wape > max_population_wape
    ):
        blockers.append("population_wape_failed")
    if (
        summary.baseline_population_wape is None
        or summary.nowcast_population_wape is None
        or summary.nowcast_population_wape > summary.baseline_population_wape
    ):
        blockers.append("population_regressed")
    if (
        summary.nowcast_level_exact_accuracy is None
        or summary.nowcast_level_exact_accuracy < min_level_exact_accuracy
    ):
        blockers.append("level_exact_failed")
    if (
        summary.baseline_level_exact_accuracy is None
        or summary.nowcast_level_exact_accuracy is None
        or summary.nowcast_level_exact_accuracy
        < summary.baseline_level_exact_accuracy
    ):
        blockers.append("level_exact_regressed")
    if (
        summary.nowcast_level_adjacent_accuracy is None
        or summary.nowcast_level_adjacent_accuracy < min_level_adjacent_accuracy
    ):
        blockers.append("level_adjacent_failed")
    if (
        summary.baseline_level_adjacent_accuracy is None
        or summary.nowcast_level_adjacent_accuracy is None
        or summary.nowcast_level_adjacent_accuracy
        < summary.baseline_level_adjacent_accuracy
    ):
        blockers.append("level_adjacent_regressed")
    quality_passed = not any(
        blocker
        for blocker in blockers
        if blocker
        not in {"insufficient_samples", "insufficient_hotspots", "insufficient_span"}
    )
    promotion_eligible = sample_sufficient and quality_passed
    return NowcastBacktestReport(
        model_version=NOWCAST_SHADOW_MODEL_VERSION,
        origins_total=len(normalized),
        samples_evaluated=samples,
        hotspots_evaluated=len(evaluated_hotspots),
        span_days=span_days,
        skip_counts=tuple(sorted(skip_counts.items())),
        mean_observation_lag_min=summary.mean_lag_min,
        lag_buckets=lag_buckets,
        nowcast_population_mae=summary.nowcast_population_mae,
        baseline_population_mae=summary.baseline_population_mae,
        nowcast_population_wape=summary.nowcast_population_wape,
        baseline_population_wape=summary.baseline_population_wape,
        nowcast_population_bias=summary.nowcast_population_bias,
        nowcast_level_exact_accuracy=summary.nowcast_level_exact_accuracy,
        baseline_level_exact_accuracy=summary.baseline_level_exact_accuracy,
        nowcast_level_adjacent_accuracy=summary.nowcast_level_adjacent_accuracy,
        baseline_level_adjacent_accuracy=(
            summary.baseline_level_adjacent_accuracy
        ),
        hybrid=_build_hybrid_comparator(metric_samples, summary),
        sample_sufficient=sample_sufficient,
        quality_passed=quality_passed,
        promotion_eligible=promotion_eligible,
        promotion_blockers=tuple(blockers),
    )
