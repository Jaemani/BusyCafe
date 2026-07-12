"""Deterministic source-local city-activity anomaly contract.

This shadow module does not claim that unlike measurements share one physical
unit.  It compares each contributor only with its own temporal baseline, and
permits aggregation only when every contributor has the same observation
type.  It deliberately emits neither a 0..100 index nor a probability.

No I/O, database model, public API, or public v1 score depends on this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import fsum, isfinite, log1p, sqrt
from typing import Literal, Sequence

from app.config import (
    ACTIVITY_SHADOW_MIN_LOG_DISPERSION,
    ACTIVITY_SHADOW_MODEL_VERSION,
    ACTIVITY_SHADOW_STANDARDIZED_ANOMALY_CAP,
    FRESHNESS_MAX_FUTURE_SKEW_MIN,
    STALE_WARN_MIN,
)


ObservationType = Literal[
    "presence_count",
    "pedestrian_flow",
    "venue_popularity",
    "transit_flow",
    "proxy",
]
ActivityMode = Literal[
    "baseline_only",
    "observed",
    "forecast",
    "unsupported",
]
FreshnessStatus = Literal["fresh", "stale", "n/a"]


@dataclass(frozen=True, slots=True)
class ActivityBaselineReference:
    """Auditable reference to the historical bucket behind one baseline."""

    model_version: str
    source_version: str
    source_hashes: tuple[str, ...]
    calendar_version: str
    window_start: date
    window_end_exclusive: date
    cutoff: date
    selected_bucket: str
    raw_n: int
    effective_n: float
    fallback_depth: int
    masked_share: float


@dataclass(frozen=True, slots=True)
class ActivityContributorInput:
    """One source-local observation and its matching temporal baseline.

    A point uses ``value``.  An interval uses ``value_min`` and ``value_max``.
    ``freshness_score`` and ``quality`` stay separate from the positive spatial or
    source ``weight`` so downstream audits can see which dimension changed.
    """

    contributor_id: str
    observation_type: ObservationType
    baseline_mean: float
    baseline_log_dispersion: float | None
    baseline_reference: ActivityBaselineReference
    value: float | None
    value_min: float | None
    value_max: float | None
    observed_at: datetime | None
    fetched_at: datetime
    weight: float
    freshness_score: float
    quality: float
    source_id: str
    source_version: str
    geometry: str
    provenance: str


@dataclass(frozen=True, slots=True)
class ActivityContributorEvidence:
    contributor_id: str
    observation_type: ObservationType
    baseline_mean: float
    baseline_log_dispersion: float | None
    baseline_reference: ActivityBaselineReference
    # Source evidence remains visible with stale freshness, but is never promoted to
    # the result's current value or anomaly fields.
    source_value: float | None
    source_value_min: float | None
    source_value_max: float | None
    anomaly_log1p: float | None
    anomaly_log1p_min: float | None
    anomaly_log1p_max: float | None
    standardized_anomaly: float | None
    standardized_anomaly_min: float | None
    standardized_anomaly_max: float | None
    observed_at: datetime | None
    fetched_at: datetime
    weight: float
    normalized_weight: float
    freshness_score: float
    quality: float
    source_id: str
    source_version: str
    geometry: str
    provenance: str


@dataclass(frozen=True, slots=True)
class ActivityShadowEstimate:
    model_version: str
    source_id: str | None
    source_version: str | None
    observation_type: ObservationType
    signal_mode: ActivityMode
    freshness: FreshnessStatus
    baseline_mean: float | None
    current_value: float | None
    current_value_min: float | None
    current_value_max: float | None
    anomaly_log1p: float | None
    anomaly_log1p_min: float | None
    anomaly_log1p_max: float | None
    standardized_anomaly: float | None
    standardized_anomaly_min: float | None
    standardized_anomaly_max: float | None
    disagreement_log1p: float | None
    observed_at_min: datetime | None
    observed_at_max: datetime | None
    fetched_at_max: datetime | None
    freshness_score: float | None
    quality: float | None
    contributors: tuple[ActivityContributorEvidence, ...]
    calibrated_probability: None = None
    is_calibrated_probability: Literal[False] = False


def _validate_finite_nonnegative(value: float, name: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def _validate_unit(value: float, name: str) -> None:
    if not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and between zero and one")


def _clip_standardized(value: float, cap: float) -> float:
    return min(cap, max(-cap, value))


def _validate_baseline_reference(reference: ActivityBaselineReference) -> None:
    if any(
        not value.strip()
        for value in (
            reference.model_version,
            reference.source_version,
            reference.calendar_version,
            reference.selected_bucket,
        )
    ):
        raise ValueError("baseline reference text fields must be non-empty")
    if not reference.source_hashes or any(
        not value.strip() for value in reference.source_hashes
    ):
        raise ValueError("baseline source_hashes must contain non-empty values")
    if reference.window_start >= reference.window_end_exclusive:
        raise ValueError("baseline window must be non-empty")
    if reference.cutoff != reference.window_end_exclusive:
        raise ValueError("baseline cutoff must equal window_end_exclusive")
    if reference.raw_n <= 0:
        raise ValueError("baseline raw_n must be positive")
    if (
        not isfinite(reference.effective_n)
        or reference.effective_n <= 0
        or reference.effective_n > reference.raw_n
    ):
        raise ValueError("baseline effective_n must be in (0, raw_n]")
    if reference.fallback_depth < 0:
        raise ValueError("baseline fallback_depth must be non-negative")
    _validate_unit(reference.masked_share, "baseline masked_share")


def _source_interval(
    contributor: ActivityContributorInput,
) -> tuple[float, float] | None:
    has_point = contributor.value is not None
    has_min = contributor.value_min is not None
    has_max = contributor.value_max is not None
    if has_point and (has_min or has_max):
        raise ValueError("value and value_min/value_max are mutually exclusive")
    if has_min != has_max:
        raise ValueError("value_min and value_max must be provided together")
    if has_point:
        assert contributor.value is not None
        _validate_finite_nonnegative(contributor.value, "value")
        return contributor.value, contributor.value
    if has_min:
        assert contributor.value_min is not None
        assert contributor.value_max is not None
        _validate_finite_nonnegative(contributor.value_min, "value_min")
        _validate_finite_nonnegative(contributor.value_max, "value_max")
        if contributor.value_min > contributor.value_max:
            raise ValueError("value_min must not exceed value_max")
        return contributor.value_min, contributor.value_max
    return None


def _validate_contributor(
    contributor: ActivityContributorInput,
    *,
    observation_type: ObservationType,
    mode: ActivityMode,
    now: datetime,
    max_future_skew_min: float,
) -> tuple[float, float] | None:
    if not contributor.contributor_id.strip():
        raise ValueError("contributor_id must be non-empty")
    if contributor.observation_type != observation_type:
        raise ValueError("different observation_type raw values cannot be combined")
    _validate_finite_nonnegative(contributor.baseline_mean, "baseline_mean")
    _validate_baseline_reference(contributor.baseline_reference)
    if contributor.baseline_log_dispersion is not None:
        _validate_finite_nonnegative(
            contributor.baseline_log_dispersion,
            "baseline_log_dispersion",
        )
    if not isfinite(contributor.weight) or contributor.weight <= 0:
        raise ValueError("weight must be finite and positive")
    _validate_unit(contributor.freshness_score, "freshness_score")
    _validate_unit(contributor.quality, "quality")
    if contributor.fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    if (
        contributor.observed_at is not None
        and contributor.observed_at.tzinfo is None
    ):
        raise ValueError("observed_at must be timezone-aware")
    if any(
        not value.strip()
        for value in (
            contributor.source_version,
            contributor.source_id,
            contributor.geometry,
            contributor.provenance,
        )
    ):
        raise ValueError(
            "source_id, source_version, geometry, and provenance must be non-empty"
        )

    future_skew = max_future_skew_min * 60.0
    if (contributor.fetched_at - now).total_seconds() > future_skew:
        raise ValueError("fetched_at exceeds allowed future skew")
    interval = _source_interval(contributor)
    if mode == "baseline_only":
        if interval is not None or contributor.observed_at is not None:
            raise ValueError("baseline_only must not contain current observations")
        return None
    if interval is None or contributor.observed_at is None:
        raise ValueError(f"{mode} requires a value and observed_at")

    if mode == "observed":
        signed_age_min = (now - contributor.observed_at).total_seconds() / 60.0
        if signed_age_min < -max_future_skew_min:
            raise ValueError("observed_at exceeds allowed future skew")
        if contributor.observed_at > contributor.fetched_at:
            raise ValueError("observed_at must not be after fetched_at")
    else:
        target_before_fetch_min = (
            contributor.fetched_at - contributor.observed_at
        ).total_seconds() / 60.0
        if target_before_fetch_min > max_future_skew_min:
            raise ValueError("forecast target must not be before fetched_at")
        target_age_min = (now - contributor.observed_at).total_seconds() / 60.0
        if target_age_min > max_future_skew_min:
            raise ValueError("forecast target has expired")
    if contributor.baseline_reference.cutoff > contributor.observed_at.date():
        raise ValueError("baseline cutoff must not be after observation date")
    return interval


def calculate_activity_shadow(
    observation_type: ObservationType,
    mode: ActivityMode,
    contributors: Sequence[ActivityContributorInput],
    *,
    now: datetime,
    standardized_cap: float = ACTIVITY_SHADOW_STANDARDIZED_ANOMALY_CAP,
    min_log_dispersion: float = ACTIVITY_SHADOW_MIN_LOG_DISPERSION,
    stale_after_min: float = STALE_WARN_MIN,
    max_future_skew_min: float = FRESHNESS_MAX_FUTURE_SKEW_MIN,
) -> ActivityShadowEstimate:
    """Calculate a source-local activity anomaly without calibrating an index."""

    allowed_types = (
        "presence_count",
        "pedestrian_flow",
        "venue_popularity",
        "transit_flow",
        "proxy",
    )
    allowed_modes = (
        "baseline_only",
        "observed",
        "forecast",
        "unsupported",
    )
    if observation_type not in allowed_types:
        raise ValueError("unsupported observation_type")
    if mode not in allowed_modes:
        raise ValueError("unsupported activity mode")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not isfinite(standardized_cap) or standardized_cap <= 0:
        raise ValueError("standardized_cap must be finite and positive")
    if not isfinite(min_log_dispersion) or min_log_dispersion <= 0:
        raise ValueError("min_log_dispersion must be finite and positive")
    if not isfinite(stale_after_min) or stale_after_min < 0:
        raise ValueError("stale_after_min must be finite and non-negative")
    if not isfinite(max_future_skew_min) or max_future_skew_min < 0:
        raise ValueError("max_future_skew_min must be finite and non-negative")
    if mode == "unsupported":
        if contributors:
            raise ValueError("unsupported mode must not contain contributors")
        return ActivityShadowEstimate(
            model_version=ACTIVITY_SHADOW_MODEL_VERSION,
            source_id=None,
            source_version=None,
            observation_type=observation_type,
            signal_mode=mode,
            freshness="n/a",
            baseline_mean=None,
            current_value=None,
            current_value_min=None,
            current_value_max=None,
            anomaly_log1p=None,
            anomaly_log1p_min=None,
            anomaly_log1p_max=None,
            standardized_anomaly=None,
            standardized_anomaly_min=None,
            standardized_anomaly_max=None,
            disagreement_log1p=None,
            observed_at_min=None,
            observed_at_max=None,
            fetched_at_max=None,
            freshness_score=None,
            quality=None,
            contributors=(),
        )
    if not contributors:
        raise ValueError(f"{mode} requires at least one contributor")

    sorted_inputs = sorted(
        contributors,
        key=lambda item: (
            item.contributor_id,
            item.source_id,
            item.source_version,
            item.geometry,
            item.provenance,
        ),
    )
    if len({item.contributor_id for item in sorted_inputs}) != len(sorted_inputs):
        raise ValueError("duplicate contributor_id")
    source_ids = {item.source_id for item in sorted_inputs}
    if len(source_ids) != 1:
        raise ValueError("different source_id raw values cannot be combined")
    source_id = next(iter(source_ids))
    source_versions = {item.source_version for item in sorted_inputs}
    if len(source_versions) != 1:
        raise ValueError("different source_version values cannot be combined")
    source_version = next(iter(source_versions))

    intervals: list[tuple[float, float] | None] = []
    for contributor in sorted_inputs:
        intervals.append(
            _validate_contributor(
                contributor,
                observation_type=observation_type,
                mode=mode,
                now=now,
                max_future_skew_min=max_future_skew_min,
            )
        )

    weight_sum = fsum(item.weight for item in sorted_inputs)
    normalized_weights = tuple(item.weight / weight_sum for item in sorted_inputs)
    # Raw counts and baselines are spatial-support-specific.  Even within one
    # source they are not an aggregateable product value; only source-local
    # anomalies may combine.  A single contributor can expose its raw evidence.
    baseline_mean = sorted_inputs[0].baseline_mean if len(sorted_inputs) == 1 else None
    aggregate_freshness = fsum(
        weight * item.freshness_score
        for item, weight in zip(sorted_inputs, normalized_weights, strict=True)
    )
    aggregate_quality = fsum(
        weight * item.quality
        for item, weight in zip(sorted_inputs, normalized_weights, strict=True)
    )

    if mode == "observed":
        stale_flags = tuple(
            item.observed_at is not None
            and (now - item.observed_at).total_seconds() / 60.0 > stale_after_min
            for item in sorted_inputs
        )
    elif mode == "forecast":
        stale_flags = tuple(
            (now - item.fetched_at).total_seconds() / 60.0 > stale_after_min
            for item in sorted_inputs
        )
    else:
        stale_flags = ()
    if stale_flags and any(stale_flags) and not all(stale_flags):
        raise ValueError(
            "mixed fresh and stale contributors require separate estimates"
        )
    is_stale = bool(stale_flags) and all(stale_flags)
    freshness_status: FreshnessStatus = (
        "n/a" if mode == "baseline_only" else "stale" if is_stale else "fresh"
    )
    # Stale source values stay in contributor evidence, but are not treated as
    # current activity and therefore cannot produce an anomaly.
    active = mode in ("observed", "forecast") and not is_stale
    anomaly_intervals: list[tuple[float, float] | None] = []
    standardized_intervals: list[tuple[float, float] | None] = []
    for item, interval in zip(sorted_inputs, intervals, strict=True):
        if not active or interval is None:
            anomaly_intervals.append(None)
            standardized_intervals.append(None)
            continue
        low, high = interval
        baseline_log = log1p(item.baseline_mean)
        anomaly_range = (log1p(low) - baseline_log, log1p(high) - baseline_log)
        anomaly_intervals.append(anomaly_range)
        dispersion = item.baseline_log_dispersion
        if dispersion is None or dispersion < min_log_dispersion:
            standardized_intervals.append(None)
        else:
            standardized_intervals.append(
                (
                    _clip_standardized(anomaly_range[0] / dispersion, standardized_cap),
                    _clip_standardized(anomaly_range[1] / dispersion, standardized_cap),
                )
            )

    evidence = tuple(
        ActivityContributorEvidence(
            contributor_id=item.contributor_id,
            observation_type=item.observation_type,
            baseline_mean=item.baseline_mean,
            baseline_log_dispersion=item.baseline_log_dispersion,
            baseline_reference=item.baseline_reference,
            source_value=item.value,
            source_value_min=item.value_min,
            source_value_max=item.value_max,
            anomaly_log1p=(
                anomaly[0] if anomaly is not None and anomaly[0] == anomaly[1] else None
            ),
            anomaly_log1p_min=anomaly[0] if anomaly is not None else None,
            anomaly_log1p_max=anomaly[1] if anomaly is not None else None,
            standardized_anomaly=(
                standardized[0]
                if standardized is not None and standardized[0] == standardized[1]
                else None
            ),
            standardized_anomaly_min=(
                standardized[0] if standardized is not None else None
            ),
            standardized_anomaly_max=(
                standardized[1] if standardized is not None else None
            ),
            observed_at=item.observed_at,
            fetched_at=item.fetched_at,
            weight=item.weight,
            normalized_weight=weight,
            freshness_score=item.freshness_score,
            quality=item.quality,
            source_id=item.source_id,
            source_version=item.source_version,
            geometry=item.geometry,
            provenance=item.provenance,
        )
        for item, weight, anomaly, standardized in zip(
            sorted_inputs,
            normalized_weights,
            anomaly_intervals,
            standardized_intervals,
            strict=True,
        )
    )

    if not active:
        observed_times = [
            item.observed_at
            for item in sorted_inputs
            if item.observed_at is not None
        ]
        return ActivityShadowEstimate(
            model_version=ACTIVITY_SHADOW_MODEL_VERSION,
            source_id=source_id,
            source_version=source_version,
            observation_type=observation_type,
            signal_mode=mode,
            freshness=freshness_status,
            baseline_mean=baseline_mean,
            current_value=None,
            current_value_min=None,
            current_value_max=None,
            anomaly_log1p=None,
            anomaly_log1p_min=None,
            anomaly_log1p_max=None,
            standardized_anomaly=None,
            standardized_anomaly_min=None,
            standardized_anomaly_max=None,
            disagreement_log1p=None,
            observed_at_min=min(observed_times) if observed_times else None,
            observed_at_max=max(observed_times) if observed_times else None,
            fetched_at_max=max(item.fetched_at for item in sorted_inputs),
            freshness_score=aggregate_freshness,
            quality=aggregate_quality,
            contributors=evidence,
        )

    concrete_intervals = [item for item in intervals if item is not None]
    concrete_anomalies = [item for item in anomaly_intervals if item is not None]
    assert len(concrete_intervals) == len(sorted_inputs)
    assert len(concrete_anomalies) == len(sorted_inputs)
    current_min = fsum(
        weight * interval[0]
        for interval, weight in zip(
            concrete_intervals, normalized_weights, strict=True
        )
    )
    current_max = fsum(
        weight * interval[1]
        for interval, weight in zip(
            concrete_intervals, normalized_weights, strict=True
        )
    )
    anomaly_min = fsum(
        weight * interval[0]
        for interval, weight in zip(
            concrete_anomalies, normalized_weights, strict=True
        )
    )
    anomaly_max = fsum(
        weight * interval[1]
        for interval, weight in zip(
            concrete_anomalies, normalized_weights, strict=True
        )
    )
    centers = tuple((low + high) / 2.0 for low, high in concrete_anomalies)
    center_mean = fsum(
        weight * center
        for center, weight in zip(centers, normalized_weights, strict=True)
    )
    disagreement = sqrt(
        max(
            0.0,
            fsum(
                weight * (center - center_mean) ** 2
                for center, weight in zip(centers, normalized_weights, strict=True)
            ),
        )
    )

    standardized_min: float | None
    standardized_max: float | None
    if all(item is not None for item in standardized_intervals):
        concrete_standardized = [
            item for item in standardized_intervals if item is not None
        ]
        standardized_min = fsum(
            weight * interval[0]
            for interval, weight in zip(
                concrete_standardized, normalized_weights, strict=True
            )
        )
        standardized_max = fsum(
            weight * interval[1]
            for interval, weight in zip(
                concrete_standardized, normalized_weights, strict=True
            )
        )
    else:
        standardized_min = standardized_max = None

    expose_raw = len(sorted_inputs) == 1
    point_result = expose_raw and current_min == current_max
    anomaly_point_result = anomaly_min == anomaly_max
    standardized_point_result = (
        standardized_min is not None and standardized_min == standardized_max
    )
    return ActivityShadowEstimate(
        model_version=ACTIVITY_SHADOW_MODEL_VERSION,
        source_id=source_id,
        source_version=source_version,
        observation_type=observation_type,
        signal_mode=mode,
        freshness=freshness_status,
        baseline_mean=baseline_mean,
        current_value=current_min if point_result else None,
        current_value_min=current_min if expose_raw else None,
        current_value_max=current_max if expose_raw else None,
        anomaly_log1p=anomaly_min if anomaly_point_result else None,
        anomaly_log1p_min=anomaly_min,
        anomaly_log1p_max=anomaly_max,
        standardized_anomaly=(
            standardized_min if standardized_point_result else None
        ),
        standardized_anomaly_min=standardized_min,
        standardized_anomaly_max=standardized_max,
        disagreement_log1p=disagreement,
        observed_at_min=min(
            item.observed_at for item in sorted_inputs if item.observed_at is not None
        ),
        observed_at_max=max(
            item.observed_at for item in sorted_inputs if item.observed_at is not None
        ),
        fetched_at_max=max(item.fetched_at for item in sorted_inputs),
        freshness_score=aggregate_freshness,
        quality=aggregate_quality,
        contributors=evidence,
    )
