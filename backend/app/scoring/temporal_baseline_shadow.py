"""Deterministic, no-leak temporal baseline for offline shadow evaluation.

The public scorer is deliberately untouched.  This module estimates what is
normal for one 250m living-population cell at one hour using only observations
strictly before an explicit cutoff.  It keeps exact ISO weekday and semantic
day type as separate features, because a Monday public holiday must not be
silently pooled with an ordinary Monday.

The hierarchy is cell-local.  Ordinary dates use exact weekday + day type,
then day type, then all dates at the requested hour.  A public/long holiday
gets an additional fallback to the same nominal ISO weekday's normal class
(working day, Saturday, or Sunday) before the all-date bucket.  Sparse fine
buckets fall back to a coarser one; an accepted fine bucket is partially
pooled toward the next bucket.  No spatial borrowing occurs here because that
requires a separately validated spatial model.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import exp, expm1, fsum, isfinite, log, log1p, sqrt
from typing import Literal

from app.config import (
    TEMPORAL_BASELINE_SHADOW_MASKED_IMPUTATION,
    TEMPORAL_BASELINE_SHADOW_MIN_BUCKET_RAW_N,
    TEMPORAL_BASELINE_SHADOW_MODEL_VERSION,
    TEMPORAL_BASELINE_SHADOW_RECENCY_HALF_LIFE_DAYS,
    TEMPORAL_BASELINE_SHADOW_SHRINKAGE_PRIOR_EFFECTIVE_N,
    TEMPORAL_BASELINE_SHADOW_SPECIAL_RECENCY_HALF_LIFE_DAYS,
    TEMPORAL_BASELINE_SHADOW_SPECIAL_WINDOW_DAYS,
    TEMPORAL_BASELINE_SHADOW_WINDOW_DAYS,
)


DayType = Literal[
    "working_day",
    "saturday",
    "sunday",
    "public_holiday",
    "long_holiday",
]
FallbackLevel = Literal[
    "iso_weekday_day_type",
    "day_type",
    "nominal_iso_weekday",
    "hour",
]


@dataclass(frozen=True, slots=True)
class TemporalDay:
    iso_weekday: int
    day_type: DayType


@dataclass(frozen=True, slots=True)
class HistoricalCellObservation:
    cell_id: str
    observed_date: date
    hour: int
    total: float | None
    masked: bool


@dataclass(frozen=True, slots=True)
class BaselineWindow:
    start_inclusive: date
    end_exclusive: date
    days: int


@dataclass(frozen=True, slots=True)
class TemporalBaselineProvenance:
    model_version: str
    transformation: str
    weighting: str
    classification: str
    selected_level: FallbackLevel | None
    masking: str
    cutoff_policy: str
    calendar_version: str
    source_version: str
    source_hashes: tuple[str, ...]
    window_days: int
    recency_half_life_days: float


@dataclass(frozen=True, slots=True)
class TemporalBaselineEstimate:
    """One baseline estimate.

    ``mean`` is back-transformed to people. ``dispersion`` remains the weighted
    standard deviation in ``log1p(people)`` space, where the fit is performed.
    ``raw_n`` and ``effective_n`` describe the selected bucket before optional
    parent shrinkage; ``masked_share`` is its unweighted source mask fraction.
    """

    cell_id: str
    target_date: date
    hour: int
    iso_weekday: int
    day_type: DayType
    mean: float | None
    dispersion: float | None
    raw_n: int
    effective_n: float
    masked_share: float | None
    fallback_depth: int | None
    window: BaselineWindow
    provenance: TemporalBaselineProvenance


@dataclass(frozen=True, slots=True)
class _Point:
    observation: HistoricalCellObservation
    day: TemporalDay
    value_log1p: float
    weight: float


@dataclass(frozen=True, slots=True)
class _Stats:
    raw_n: int
    effective_n: float
    masked_share: float
    mean: float
    variance: float


def _is_non_working(value: date, public_holidays: frozenset[date]) -> bool:
    return value.isoweekday() >= 6 or value in public_holidays


def classify_temporal_day(
    value: date,
    public_holidays: Collection[date],
) -> TemporalDay:
    """Classify a date without I/O or assumptions about a holiday provider.

    A ``long_holiday`` is every day in a block of at least three consecutive
    non-working days where the block contains an official public holiday.
    Callers must therefore include substitute holidays in ``public_holidays``.
    A normal weekend remains Saturday/Sunday rather than a long holiday.
    """

    holidays = frozenset(public_holidays)
    if any(not isinstance(item, date) for item in holidays):
        raise TypeError("public_holidays must contain date values")

    iso_weekday = value.isoweekday()
    if _is_non_working(value, holidays):
        block_start = value
        while _is_non_working(block_start - timedelta(days=1), holidays):
            block_start -= timedelta(days=1)
        block_end = value
        while _is_non_working(block_end + timedelta(days=1), holidays):
            block_end += timedelta(days=1)
        block_days = (block_end - block_start).days + 1
        contains_official_holiday = any(
            block_start <= holiday <= block_end for holiday in holidays
        )
        if block_days >= 3 and contains_official_holiday:
            return TemporalDay(iso_weekday=iso_weekday, day_type="long_holiday")
        if value in holidays:
            return TemporalDay(iso_weekday=iso_weekday, day_type="public_holiday")
        if iso_weekday == 6:
            return TemporalDay(iso_weekday=iso_weekday, day_type="saturday")
        return TemporalDay(iso_weekday=iso_weekday, day_type="sunday")
    return TemporalDay(iso_weekday=iso_weekday, day_type="working_day")


def _validate_parameters(
    *,
    cell_id: str,
    target_date: date,
    hour: int,
    cutoff: date,
    window_days: int,
    recency_half_life_days: float,
    min_bucket_raw_n: int,
    shrinkage_prior_effective_n: float,
    masked_imputation: float,
    calendar_version: str,
    source_version: str,
    source_hashes: Sequence[str],
) -> None:
    if not cell_id.strip():
        raise ValueError("cell_id must be non-empty")
    if not 0 <= hour <= 23:
        raise ValueError("hour must be in 0..23")
    if cutoff > target_date:
        raise ValueError("cutoff must not be after target_date")
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    if not isfinite(recency_half_life_days) or recency_half_life_days <= 0:
        raise ValueError("recency_half_life_days must be finite and positive")
    if min_bucket_raw_n <= 0:
        raise ValueError("min_bucket_raw_n must be positive")
    if (
        not isfinite(shrinkage_prior_effective_n)
        or shrinkage_prior_effective_n < 0
    ):
        raise ValueError("shrinkage_prior_effective_n must be finite and non-negative")
    if not isfinite(masked_imputation) or masked_imputation < 0:
        raise ValueError("masked_imputation must be finite and non-negative")
    if not calendar_version.strip():
        raise ValueError("calendar_version must be non-empty")
    if not source_version.strip():
        raise ValueError("source_version must be non-empty")
    if not source_hashes or any(not item.strip() for item in source_hashes):
        raise ValueError("source_hashes must contain non-empty values")


def _validate_observation(observation: HistoricalCellObservation) -> None:
    if not observation.cell_id.strip():
        raise ValueError("observation cell_id must be non-empty")
    if not 0 <= observation.hour <= 23:
        raise ValueError("observation hour must be in 0..23")
    if observation.masked:
        if observation.total is not None:
            raise ValueError("masked observation total must be None")
        return
    if observation.total is None:
        raise ValueError("unmasked observation total must not be None")
    if not isfinite(observation.total) or observation.total < 0:
        raise ValueError("observation total must be finite and non-negative")


def _stats(points: Sequence[_Point]) -> _Stats:
    weights = [point.weight for point in points]
    weight_sum = fsum(weights)
    weighted_values = [
        point.weight * point.value_log1p for point in points
    ]
    mean = fsum(weighted_values) / weight_sum
    variance = fsum(
        point.weight * (point.value_log1p - mean) ** 2 for point in points
    ) / weight_sum
    effective_n = weight_sum**2 / fsum(weight**2 for weight in weights)
    return _Stats(
        raw_n=len(points),
        effective_n=effective_n,
        masked_share=sum(point.observation.masked for point in points) / len(points),
        mean=mean,
        variance=max(0.0, variance),
    )


def estimate_temporal_baseline_shadow(
    cell_id: str,
    target_date: date,
    hour: int,
    observations: Sequence[HistoricalCellObservation],
    *,
    cutoff: date,
    public_holidays: Collection[date],
    calendar_version: str,
    source_version: str,
    source_hashes: Sequence[str],
    window_days: int | None = None,
    recency_half_life_days: float | None = None,
    min_bucket_raw_n: int = TEMPORAL_BASELINE_SHADOW_MIN_BUCKET_RAW_N,
    shrinkage_prior_effective_n: float = (
        TEMPORAL_BASELINE_SHADOW_SHRINKAGE_PRIOR_EFFECTIVE_N
    ),
    masked_imputation: float = TEMPORAL_BASELINE_SHADOW_MASKED_IMPUTATION,
) -> TemporalBaselineEstimate:
    """Estimate a cell/hour baseline using observations before ``cutoff`` only."""

    holidays = frozenset(public_holidays)
    target_day = classify_temporal_day(target_date, holidays)
    is_special_target = target_day.day_type in (
        "public_holiday",
        "long_holiday",
    )
    selected_window_days = (
        window_days
        if window_days is not None
        else (
            TEMPORAL_BASELINE_SHADOW_SPECIAL_WINDOW_DAYS
            if is_special_target
            else TEMPORAL_BASELINE_SHADOW_WINDOW_DAYS
        )
    )
    selected_half_life_days = (
        recency_half_life_days
        if recency_half_life_days is not None
        else (
            TEMPORAL_BASELINE_SHADOW_SPECIAL_RECENCY_HALF_LIFE_DAYS
            if is_special_target
            else TEMPORAL_BASELINE_SHADOW_RECENCY_HALF_LIFE_DAYS
        )
    )
    _validate_parameters(
        cell_id=cell_id,
        target_date=target_date,
        hour=hour,
        cutoff=cutoff,
        window_days=selected_window_days,
        recency_half_life_days=selected_half_life_days,
        min_bucket_raw_n=min_bucket_raw_n,
        shrinkage_prior_effective_n=shrinkage_prior_effective_n,
        masked_imputation=masked_imputation,
        calendar_version=calendar_version,
        source_version=source_version,
        source_hashes=source_hashes,
    )
    window_start = cutoff - timedelta(days=selected_window_days)
    window = BaselineWindow(window_start, cutoff, selected_window_days)

    seen: set[tuple[str, date, int]] = set()
    eligible: list[_Point] = []
    for observation in observations:
        _validate_observation(observation)
        identity = (
            observation.cell_id,
            observation.observed_date,
            observation.hour,
        )
        if identity in seen:
            raise ValueError(f"duplicate observation: {identity!r}")
        seen.add(identity)
        if (
            observation.cell_id != cell_id
            or observation.hour != hour
            or observation.observed_date < window_start
            or observation.observed_date >= cutoff
        ):
            continue
        value = masked_imputation if observation.masked else observation.total
        assert value is not None
        age_days = (cutoff - observation.observed_date).days
        weight = exp(-log(2.0) * age_days / selected_half_life_days)
        eligible.append(
            _Point(
                observation=observation,
                day=classify_temporal_day(observation.observed_date, holidays),
                value_log1p=log1p(value),
                weight=weight,
            )
        )

    # Stable sort plus fsum makes results independent of caller ordering.
    eligible.sort(
        key=lambda point: (
            point.observation.observed_date,
            point.observation.cell_id,
            point.observation.hour,
            point.observation.masked,
            point.value_log1p,
        )
    )
    exact_bucket: tuple[FallbackLevel, list[_Point]] = (
        "iso_weekday_day_type",
        [
            point
            for point in eligible
            if point.day.iso_weekday == target_day.iso_weekday
            and point.day.day_type == target_day.day_type
        ],
    )
    same_type_bucket: tuple[FallbackLevel, list[_Point]] = (
        "day_type",
        [point for point in eligible if point.day.day_type == target_day.day_type],
    )
    hour_bucket: tuple[FallbackLevel, list[_Point]] = ("hour", eligible)
    if target_day.day_type in ("public_holiday", "long_holiday"):
        nominal_day_type: DayType
        if target_day.iso_weekday <= 5:
            nominal_day_type = "working_day"
        elif target_day.iso_weekday == 6:
            nominal_day_type = "saturday"
        else:
            nominal_day_type = "sunday"
        nominal_bucket: tuple[FallbackLevel, list[_Point]] = (
            "nominal_iso_weekday",
            [
                point
                for point in eligible
                if point.day.iso_weekday == target_day.iso_weekday
                and point.day.day_type == nominal_day_type
            ],
        )
        buckets: tuple[tuple[FallbackLevel, list[_Point]], ...] = (
            exact_bucket,
            same_type_bucket,
            nominal_bucket,
            hour_bucket,
        )
    else:
        buckets = (
            exact_bucket,
            same_type_bucket,
            hour_bucket,
        )

    chosen_depth: int | None = next(
        (
            depth
            for depth, (_, points) in enumerate(buckets)
            if len(points) >= min_bucket_raw_n
        ),
        None,
    )
    if chosen_depth is None:
        # Fail soft only within the coarsest cell/hour bucket.  We expose the
        # low sample size rather than inventing a spatial/global prior.
        chosen_depth = len(buckets) - 1 if eligible else None

    selected_level = buckets[chosen_depth][0] if chosen_depth is not None else None
    provenance = TemporalBaselineProvenance(
        model_version=TEMPORAL_BASELINE_SHADOW_MODEL_VERSION,
        transformation="log1p",
        weighting=f"exponential-half-life-days:{selected_half_life_days:g}",
        classification="iso-weekday+day-type;long-holiday-block>=3-v1",
        selected_level=selected_level,
        masking=f"constant-imputation:{masked_imputation:g}",
        cutoff_policy="observed_date<cutoff;no-interpolation",
        calendar_version=calendar_version,
        source_version=source_version,
        source_hashes=tuple(source_hashes),
        window_days=selected_window_days,
        recency_half_life_days=selected_half_life_days,
    )
    if chosen_depth is None:
        return TemporalBaselineEstimate(
            cell_id=cell_id,
            target_date=target_date,
            hour=hour,
            iso_weekday=target_day.iso_weekday,
            day_type=target_day.day_type,
            mean=None,
            dispersion=None,
            raw_n=0,
            effective_n=0.0,
            masked_share=None,
            fallback_depth=None,
            window=window,
            provenance=provenance,
        )

    selected_stats = _stats(buckets[chosen_depth][1])
    fitted_mean = selected_stats.mean
    fitted_variance = selected_stats.variance
    parent_points = next(
        (
            points
            for _, points in buckets[chosen_depth + 1 :]
            if points
        ),
        None,
    )
    if parent_points is not None and shrinkage_prior_effective_n > 0:
        parent_stats = _stats(parent_points)
        alpha = selected_stats.effective_n / (
            selected_stats.effective_n + shrinkage_prior_effective_n
        )
        combined_mean = alpha * selected_stats.mean + (1.0 - alpha) * parent_stats.mean
        fitted_variance = (
            alpha
            * (
                selected_stats.variance
                + (selected_stats.mean - combined_mean) ** 2
            )
            + (1.0 - alpha)
            * (parent_stats.variance + (parent_stats.mean - combined_mean) ** 2)
        )
        fitted_mean = combined_mean

    return TemporalBaselineEstimate(
        cell_id=cell_id,
        target_date=target_date,
        hour=hour,
        iso_weekday=target_day.iso_weekday,
        day_type=target_day.day_type,
        mean=max(0.0, expm1(fitted_mean)),
        dispersion=sqrt(max(0.0, fitted_variance)),
        raw_n=selected_stats.raw_n,
        effective_n=selected_stats.effective_n,
        masked_share=selected_stats.masked_share,
        fallback_depth=chosen_depth,
        window=window,
        provenance=provenance,
    )
