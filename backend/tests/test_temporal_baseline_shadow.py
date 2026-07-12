from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config import (
    TEMPORAL_BASELINE_SHADOW_RECENCY_HALF_LIFE_DAYS,
    TEMPORAL_BASELINE_SHADOW_SPECIAL_RECENCY_HALF_LIFE_DAYS,
    TEMPORAL_BASELINE_SHADOW_SPECIAL_WINDOW_DAYS,
    TEMPORAL_BASELINE_SHADOW_WINDOW_DAYS,
)
from app.scoring.temporal_baseline_shadow import (
    HistoricalCellObservation,
    classify_temporal_day,
    estimate_temporal_baseline_shadow,
)


CELL = "다사52505325"
TARGET = date(2026, 7, 13)  # Monday
CALENDAR_VERSION = "kr-public-holidays-fixture-v1"
SOURCE_VERSION = "oa-22784-fixture-v1"
SOURCE_HASHES = ("sha256:fixture-a", "sha256:fixture-b")


def observation(
    days_before: int,
    total: float | None,
    *,
    masked: bool = False,
    hour: int = 14,
    cell_id: str = CELL,
) -> HistoricalCellObservation:
    return HistoricalCellObservation(
        cell_id=cell_id,
        observed_date=TARGET - timedelta(days=days_before),
        hour=hour,
        total=total,
        masked=masked,
    )


def test_classifies_exact_weekday_public_holiday_and_long_holiday_block() -> None:
    ordinary = classify_temporal_day(date(2026, 7, 13), set())
    assert ordinary.iso_weekday == 1
    assert ordinary.day_type == "working_day"

    isolated_holiday = date(2026, 7, 15)
    holiday = classify_temporal_day(isolated_holiday, {isolated_holiday})
    assert holiday.iso_weekday == 3
    assert holiday.day_type == "public_holiday"

    # Friday holiday + normal weekend is a three-day non-working block; every
    # date in that block is classified consistently as long_holiday.
    friday = date(2026, 7, 17)
    assert classify_temporal_day(friday, {friday}).day_type == "long_holiday"
    assert classify_temporal_day(friday + timedelta(days=1), {friday}).day_type == (
        "long_holiday"
    )
    assert classify_temporal_day(friday + timedelta(days=2), {friday}).day_type == (
        "long_holiday"
    )

    saturday = date(2026, 7, 11)
    assert classify_temporal_day(saturday, set()).day_type == "saturday"
    assert classify_temporal_day(saturday + timedelta(days=1), set()).day_type == (
        "sunday"
    )


def test_sparse_exact_weekday_falls_back_to_day_type_bucket() -> None:
    observations = [
        observation(7, 100.0),  # one Monday: exact bucket is sparse
        observation(4, 200.0),  # Thursday
        observation(3, 300.0),  # Friday
    ]

    result = estimate_temporal_baseline_shadow(
        CELL,
        TARGET,
        14,
        observations,
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=2,
        shrinkage_prior_effective_n=0,
    )

    assert result.fallback_depth == 1
    assert result.provenance.selected_level == "day_type"
    assert result.raw_n == 3
    assert result.mean is not None


def test_cutoff_and_window_exclude_target_future_and_old_observations() -> None:
    observations = [
        observation(7, 100.0),
        observation(14, 100.0),
        observation(70, 9999.0),  # outside 30-day rolling window
        observation(0, 9999.0),  # cutoff date itself
        observation(-1, 9999.0),  # future
    ]

    result = estimate_temporal_baseline_shadow(
        CELL,
        TARGET,
        14,
        observations,
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        window_days=30,
        min_bucket_raw_n=1,
        shrinkage_prior_effective_n=0,
    )

    assert result.raw_n == 2
    assert result.mean == pytest.approx(100.0)
    assert result.window.start_inclusive == TARGET - timedelta(days=30)
    assert result.window.end_exclusive == TARGET
    assert result.provenance.window_days == 30
    assert result.provenance.recency_half_life_days == (
        TEMPORAL_BASELINE_SHADOW_RECENCY_HALF_LIFE_DAYS
    )


def test_special_target_uses_long_history_while_ordinary_target_does_not() -> None:
    prior_same_weekday = TARGET - timedelta(days=364)
    observations = [
        observation(364, 120.0),
        observation(0, 9_999.0),  # cutoff date must still be excluded
        observation(-1, 9_999.0),  # future must still be excluded
    ]

    special = estimate_temporal_baseline_shadow(
        CELL,
        TARGET,
        14,
        observations,
        cutoff=TARGET,
        public_holidays={TARGET, prior_same_weekday},
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=1,
        shrinkage_prior_effective_n=0,
    )
    ordinary = estimate_temporal_baseline_shadow(
        CELL,
        TARGET,
        14,
        observations,
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=1,
        shrinkage_prior_effective_n=0,
    )

    assert special.day_type == "long_holiday"
    assert special.raw_n == 1
    assert special.mean == pytest.approx(120.0)
    assert special.window.days == TEMPORAL_BASELINE_SHADOW_SPECIAL_WINDOW_DAYS
    assert special.provenance.window_days == (
        TEMPORAL_BASELINE_SHADOW_SPECIAL_WINDOW_DAYS
    )
    assert special.provenance.recency_half_life_days == (
        TEMPORAL_BASELINE_SHADOW_SPECIAL_RECENCY_HALF_LIFE_DAYS
    )

    assert ordinary.day_type == "working_day"
    assert ordinary.raw_n == 0
    assert ordinary.mean is None
    assert ordinary.window.days == TEMPORAL_BASELINE_SHADOW_WINDOW_DAYS


def test_result_is_deterministic_and_input_order_independent() -> None:
    observations = [
        observation(7, 80.0),
        observation(14, 120.0),
        observation(21, 200.0),
        observation(4, 350.0),
    ]
    kwargs = dict(
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=2,
    )

    first = estimate_temporal_baseline_shadow(CELL, TARGET, 14, observations, **kwargs)
    second = estimate_temporal_baseline_shadow(
        CELL, TARGET, 14, list(reversed(observations)), **kwargs
    )

    assert first == second
    assert first.provenance.transformation == "log1p"
    assert first.provenance.calendar_version == CALENDAR_VERSION
    assert first.provenance.source_version == SOURCE_VERSION
    assert first.provenance.source_hashes == SOURCE_HASHES
    assert first.effective_n <= first.raw_n


def test_special_day_falls_back_to_nominal_weekday_before_all_dates() -> None:
    target = date(2026, 7, 15)  # Wednesday, isolated from the weekend
    prior_special_day = target - timedelta(days=7)
    holidays = {target, prior_special_day}
    observations = [
        HistoricalCellObservation(
            CELL, target - timedelta(days=14), 14, 100.0, False
        ),
        HistoricalCellObservation(
            CELL, target - timedelta(days=21), 14, 100.0, False
        ),
        HistoricalCellObservation(
            CELL, prior_special_day, 14, 900.0, False
        ),
    ]

    result = estimate_temporal_baseline_shadow(
        CELL,
        target,
        14,
        observations,
        cutoff=target,
        public_holidays=holidays,
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=2,
        shrinkage_prior_effective_n=0,
    )

    assert result.day_type == "public_holiday"
    assert result.fallback_depth == 2
    assert result.provenance.selected_level == "nominal_iso_weekday"
    assert result.raw_n == 2
    assert result.mean == pytest.approx(100.0)


def test_special_type_can_shrink_when_nominal_bucket_is_empty() -> None:
    target = date(2026, 7, 15)  # Wednesday, isolated from the weekend
    first_holiday = target - timedelta(days=8)  # Tuesday
    second_holiday = target - timedelta(days=13)  # Thursday
    holidays = {target, first_holiday, second_holiday}
    observations = [
        HistoricalCellObservation(CELL, first_holiday, 14, 200.0, False),
        HistoricalCellObservation(CELL, second_holiday, 14, 300.0, False),
    ]

    result = estimate_temporal_baseline_shadow(
        CELL,
        target,
        14,
        observations,
        cutoff=target,
        public_holidays=holidays,
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=2,
    )

    assert result.fallback_depth == 1
    assert result.provenance.selected_level == "day_type"
    assert result.mean is not None


def test_recency_weighting_favors_recent_history_in_log_space() -> None:
    observations = [
        observation(7, 300.0),
        observation(14, 10.0),
    ]

    recency_weighted = estimate_temporal_baseline_shadow(
        CELL,
        TARGET,
        14,
        observations,
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=1,
        recency_half_life_days=7,
        shrinkage_prior_effective_n=0,
    )
    nearly_unweighted = estimate_temporal_baseline_shadow(
        CELL,
        TARGET,
        14,
        observations,
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=1,
        recency_half_life_days=1_000_000,
        shrinkage_prior_effective_n=0,
    )

    assert recency_weighted.mean is not None
    assert nearly_unweighted.mean is not None
    assert recency_weighted.mean > nearly_unweighted.mean


def test_fine_bucket_is_shrunk_toward_immediate_parent() -> None:
    observations = [
        observation(7, 100.0),
        observation(14, 100.0),
        observation(4, 900.0),
        observation(3, 900.0),
    ]
    common = dict(
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=2,
        recency_half_life_days=1_000_000,
    )

    unshrunk = estimate_temporal_baseline_shadow(
        CELL, TARGET, 14, observations, shrinkage_prior_effective_n=0, **common
    )
    shrunk = estimate_temporal_baseline_shadow(
        CELL, TARGET, 14, observations, shrinkage_prior_effective_n=4, **common
    )

    assert unshrunk.fallback_depth == shrunk.fallback_depth == 0
    assert unshrunk.mean == pytest.approx(100.0)
    assert shrunk.mean is not None
    assert unshrunk.mean < shrunk.mean < 900.0


def test_masking_sensitivity_changes_fit_but_preserves_quality_metadata() -> None:
    observations = [
        observation(7, 10.0),
        observation(14, None, masked=True),
        observation(21, None, masked=True),
    ]
    common = dict(
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
        min_bucket_raw_n=1,
        shrinkage_prior_effective_n=0,
    )

    low = estimate_temporal_baseline_shadow(
        CELL, TARGET, 14, observations, masked_imputation=0.0, **common
    )
    high = estimate_temporal_baseline_shadow(
        CELL, TARGET, 14, observations, masked_imputation=3.0, **common
    )

    assert low.mean is not None and high.mean is not None
    assert high.mean > low.mean
    assert low.raw_n == high.raw_n == 3
    assert low.masked_share == high.masked_share == pytest.approx(2 / 3)
    assert low.provenance.masking == "constant-imputation:0"
    assert high.provenance.masking == "constant-imputation:3"


def test_empty_history_fails_closed_without_inventing_global_prior() -> None:
    result = estimate_temporal_baseline_shadow(
        CELL,
        TARGET,
        14,
        [],
        cutoff=TARGET,
        public_holidays=set(),
        calendar_version=CALENDAR_VERSION,
        source_version=SOURCE_VERSION,
        source_hashes=SOURCE_HASHES,
    )

    assert result.mean is None
    assert result.dispersion is None
    assert result.raw_n == 0
    assert result.effective_n == 0
    assert result.masked_share is None
    assert result.fallback_depth is None
    assert result.provenance.selected_level is None


def test_invalid_mask_contract_and_duplicate_observation_fail_closed() -> None:
    with pytest.raises(ValueError, match="masked observation total must be None"):
        estimate_temporal_baseline_shadow(
            CELL,
            TARGET,
            14,
            [observation(7, 2.0, masked=True)],
            cutoff=TARGET,
            public_holidays=set(),
            calendar_version=CALENDAR_VERSION,
            source_version=SOURCE_VERSION,
            source_hashes=SOURCE_HASHES,
        )

    duplicate = observation(7, 10.0)
    with pytest.raises(ValueError, match="duplicate observation"):
        estimate_temporal_baseline_shadow(
            CELL,
            TARGET,
            14,
            [duplicate, duplicate],
            cutoff=TARGET,
            public_holidays=set(),
            calendar_version=CALENDAR_VERSION,
            source_version=SOURCE_VERSION,
            source_hashes=SOURCE_HASHES,
        )


def test_provenance_versions_are_required_and_non_empty() -> None:
    with pytest.raises(ValueError, match="calendar_version must be non-empty"):
        estimate_temporal_baseline_shadow(
            CELL,
            TARGET,
            14,
            [],
            cutoff=TARGET,
            public_holidays=set(),
            calendar_version=" ",
            source_version=SOURCE_VERSION,
            source_hashes=SOURCE_HASHES,
        )
    with pytest.raises(ValueError, match="source_hashes"):
        estimate_temporal_baseline_shadow(
            CELL,
            TARGET,
            14,
            [],
            cutoff=TARGET,
            public_holidays=set(),
            calendar_version=CALENDAR_VERSION,
            source_version=SOURCE_VERSION,
            source_hashes=(),
        )
