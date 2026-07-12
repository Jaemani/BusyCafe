from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from math import log1p

import pytest

from app.config import ACTIVITY_SHADOW_MODEL_VERSION
from app.scoring.activity_shadow import (
    ActivityBaselineReference,
    ActivityContributorInput,
    calculate_activity_shadow,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def baseline_reference() -> ActivityBaselineReference:
    return ActivityBaselineReference(
        model_version="temporal-baseline-shadow-v1",
        source_version="fixture-history-v1",
        source_hashes=("sha256:fixture",),
        calendar_version="kr-holidays-fixture-v1",
        window_start=date(2026, 6, 13),
        window_end_exclusive=date(2026, 7, 13),
        cutoff=date(2026, 7, 13),
        selected_bucket="iso_weekday_day_type",
        raw_n=4,
        effective_n=3.5,
        fallback_depth=0,
        masked_share=0.0,
    )


def contributor(
    contributor_id: str = "cell-1",
    *,
    observation_type: str = "presence_count",
    baseline_mean: float = 100.0,
    dispersion: float | None = 0.5,
    value: float | None = 200.0,
    value_min: float | None = None,
    value_max: float | None = None,
    age_min: float | None = 5.0,
    fetched_age_min: float = 1.0,
    weight: float = 1.0,
    freshness: float = 0.9,
    quality: float = 0.8,
) -> ActivityContributorInput:
    return ActivityContributorInput(
        contributor_id=contributor_id,
        observation_type=observation_type,  # type: ignore[arg-type]
        baseline_mean=baseline_mean,
        baseline_log_dispersion=dispersion,
        baseline_reference=baseline_reference(),
        value=value,
        value_min=value_min,
        value_max=value_max,
        observed_at=NOW - timedelta(minutes=age_min) if age_min is not None else None,
        fetched_at=NOW - timedelta(minutes=fetched_age_min),
        weight=weight,
        freshness_score=freshness,
        quality=quality,
        source_id="seoul-living-population",
        source_version="fixture-source-v1",
        geometry="grid:250m:cell-1",
        provenance="fixture:row-1",
    )


def test_point_observation_uses_source_local_log1p_anomaly() -> None:
    item = contributor()

    result = calculate_activity_shadow(
        "presence_count", "observed", [item], now=NOW
    )

    expected = log1p(200.0) - log1p(100.0)
    assert result.model_version == ACTIVITY_SHADOW_MODEL_VERSION
    assert result.source_id == "seoul-living-population"
    assert result.source_version == "fixture-source-v1"
    assert result.signal_mode == "observed"
    assert result.freshness == "fresh"
    assert result.current_value == 200.0
    assert result.current_value_min == result.current_value_max == 200.0
    assert result.anomaly_log1p == pytest.approx(expected)
    assert result.standardized_anomaly == pytest.approx(expected / 0.5)
    assert result.disagreement_log1p == 0.0
    assert result.observed_at_min == result.observed_at_max == item.observed_at
    assert result.fetched_at_max == item.fetched_at
    assert result.calibrated_probability is None
    assert result.is_calibrated_probability is False


def test_interval_preserves_anomaly_envelope_without_inventing_point() -> None:
    item = contributor(value=None, value_min=80.0, value_max=140.0)

    result = calculate_activity_shadow(
        "presence_count", "observed", [item], now=NOW
    )

    assert result.current_value is None
    assert result.current_value_min == 80.0
    assert result.current_value_max == 140.0
    assert result.anomaly_log1p is None
    assert result.anomaly_log1p_min == pytest.approx(log1p(80) - log1p(100))
    assert result.anomaly_log1p_max == pytest.approx(log1p(140) - log1p(100))
    assert result.standardized_anomaly is None
    assert result.standardized_anomaly_min is not None
    assert result.standardized_anomaly_max is not None


def test_standardized_anomaly_requires_dispersion_and_is_capped() -> None:
    missing = calculate_activity_shadow(
        "presence_count",
        "observed",
        [contributor(dispersion=None)],
        now=NOW,
    )
    too_small = calculate_activity_shadow(
        "presence_count",
        "observed",
        [contributor(dispersion=0.0)],
        now=NOW,
    )
    capped = calculate_activity_shadow(
        "presence_count",
        "observed",
        [contributor(baseline_mean=0.0, dispersion=0.01, value=1_000.0)],
        now=NOW,
        standardized_cap=3.0,
    )

    assert missing.standardized_anomaly is None
    assert too_small.standardized_anomaly is None
    assert capped.standardized_anomaly == 3.0


def test_same_type_anomaly_is_order_independent_without_raw_aggregate() -> None:
    first = contributor(
        "a", baseline_mean=100, value=200, weight=1, freshness=0.9, quality=0.7
    )
    second = contributor(
        "b", baseline_mean=300, value=150, weight=3, freshness=0.5, quality=0.9
    )

    forward = calculate_activity_shadow(
        "presence_count", "observed", [first, second], now=NOW
    )
    reverse = calculate_activity_shadow(
        "presence_count", "observed", [second, first], now=NOW
    )

    anomaly_a = log1p(200) - log1p(100)
    anomaly_b = log1p(150) - log1p(300)
    assert forward == reverse
    assert forward.baseline_mean is None
    assert forward.current_value is None
    assert forward.current_value_min is None
    assert forward.current_value_max is None
    assert forward.anomaly_log1p == pytest.approx(0.25 * anomaly_a + 0.75 * anomaly_b)
    assert forward.disagreement_log1p is not None
    assert forward.disagreement_log1p > 0
    assert forward.freshness_score == pytest.approx(0.6)
    assert forward.quality == pytest.approx(0.85)
    assert [item.contributor_id for item in forward.contributors] == ["a", "b"]
    assert [item.normalized_weight for item in forward.contributors] == [0.25, 0.75]
    assert forward.contributors[0].source_version == "fixture-source-v1"
    assert forward.contributors[0].source_id == "seoul-living-population"
    assert forward.contributors[0].geometry == "grid:250m:cell-1"
    assert forward.contributors[0].provenance == "fixture:row-1"
    assert forward.contributors[0].baseline_reference == baseline_reference()


def test_different_observation_types_never_combine_raw_values() -> None:
    with pytest.raises(ValueError, match="different observation_type"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [contributor(), contributor("flow", observation_type="pedestrian_flow")],
            now=NOW,
        )


def test_different_sources_never_combine_even_with_same_observation_type() -> None:
    with pytest.raises(ValueError, match="different source_id"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [contributor("a"), replace(contributor("b"), source_id="other-provider")],
            now=NOW,
        )


def test_different_source_versions_never_combine() -> None:
    with pytest.raises(ValueError, match="different source_version"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [
                contributor("a"),
                replace(contributor("b"), source_version="other-release"),
            ],
            now=NOW,
        )


def test_baseline_only_has_no_current_value_or_anomaly() -> None:
    baseline = contributor(value=None, age_min=None)
    result = calculate_activity_shadow(
        "presence_count", "baseline_only", [baseline], now=NOW
    )

    assert result.freshness == "n/a"
    assert result.source_id == "seoul-living-population"
    assert result.observed_at_min is None
    assert result.observed_at_max is None
    assert result.fetched_at_max == baseline.fetched_at
    assert result.baseline_mean == 100.0
    assert result.current_value is None
    assert result.anomaly_log1p is None
    assert result.standardized_anomaly is None
    assert result.disagreement_log1p is None
    assert result.contributors[0].source_value is None


def test_stale_observed_value_stays_evidence_and_is_not_promoted() -> None:
    stale = contributor(value=250.0, age_min=30.0, fetched_age_min=29.0)

    result = calculate_activity_shadow(
        "presence_count", "observed", [stale], now=NOW, stale_after_min=25
    )

    assert result.signal_mode == "observed"
    assert result.freshness == "stale"
    assert result.current_value is None
    assert result.current_value_min is None
    assert result.anomaly_log1p is None
    assert result.standardized_anomaly is None
    assert result.observed_at_min == result.observed_at_max == stale.observed_at
    assert result.fetched_at_max == stale.fetched_at
    assert result.contributors[0].source_value == 250.0
    assert result.contributors[0].anomaly_log1p is None


def test_stale_forecast_is_also_not_promoted() -> None:
    forecast = replace(
        contributor(age_min=-60.0, fetched_age_min=30.0),
        observed_at=NOW + timedelta(hours=1),
    )
    result = calculate_activity_shadow(
        "presence_count", "forecast", [forecast], now=NOW, stale_after_min=25
    )

    assert result.signal_mode == "forecast"
    assert result.freshness == "stale"
    assert result.current_value is None


def test_fresh_future_forecast_is_supported() -> None:
    forecast = replace(
        contributor(age_min=-60.0),
        observed_at=NOW + timedelta(hours=1),
    )
    result = calculate_activity_shadow(
        "presence_count", "forecast", [forecast], now=NOW
    )
    assert result.freshness == "fresh"
    assert result.current_value == 200.0


def test_expired_or_pre_generation_forecast_fails_closed() -> None:
    expired = replace(
        contributor(),
        observed_at=NOW - timedelta(minutes=10),
        fetched_at=NOW - timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="forecast target has expired"):
        calculate_activity_shadow(
            "presence_count",
            "forecast",
            [expired],
            now=NOW,
            max_future_skew_min=5,
        )

    before_fetch = replace(
        contributor(),
        observed_at=NOW - timedelta(minutes=5),
        fetched_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="forecast target must not be before"):
        calculate_activity_shadow(
            "presence_count",
            "forecast",
            [before_fetch],
            now=NOW,
            max_future_skew_min=5,
        )


def test_mixed_freshness_contributors_require_separate_estimates() -> None:
    fresh = contributor("fresh", age_min=5)
    stale = contributor("stale", age_min=30, fetched_age_min=29)
    with pytest.raises(ValueError, match="mixed fresh and stale"):
        calculate_activity_shadow(
            "presence_count", "observed", [fresh, stale], now=NOW
        )


def test_unsupported_has_no_contributors_or_invented_values() -> None:
    result = calculate_activity_shadow(
        "proxy", "unsupported", [], now=NOW
    )
    assert result.freshness == "n/a"
    assert result.source_id is None
    assert result.observed_at_min is None
    assert result.observed_at_max is None
    assert result.fetched_at_max is None
    assert result.baseline_mean is None
    assert result.current_value is None
    assert result.contributors == ()

    with pytest.raises(ValueError, match="must not contain contributors"):
        calculate_activity_shadow(
            "proxy", "unsupported", [contributor(observation_type="proxy")], now=NOW
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"baseline_mean": -1.0}, "baseline_mean"),
        ({"value": -1.0}, "value"),
        ({"weight": 0.0}, "weight"),
        ({"freshness_score": 1.1}, "freshness_score"),
        ({"quality": float("nan")}, "quality"),
        ({"source_version": ""}, "source_version"),
        ({"source_id": ""}, "source_id"),
    ],
)
def test_invalid_numeric_or_provenance_inputs_fail_closed(change, message) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_activity_shadow(
            "presence_count", "observed", [replace(contributor(), **change)], now=NOW
        )


def test_invalid_or_leaking_baseline_reference_fails_closed() -> None:
    with pytest.raises(ValueError, match="effective_n"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [
                replace(
                    contributor(),
                    baseline_reference=replace(
                        baseline_reference(), effective_n=5.0
                    ),
                )
            ],
            now=NOW,
        )

    leaking = replace(
        baseline_reference(),
        window_end_exclusive=date(2026, 7, 14),
        cutoff=date(2026, 7, 14),
    )
    with pytest.raises(ValueError, match="after observation date"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [replace(contributor(), baseline_reference=leaking)],
            now=NOW,
        )


def test_invalid_value_shapes_fail_closed() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [contributor(value=1, value_min=1, value_max=2)],
            now=NOW,
        )
    with pytest.raises(ValueError, match="provided together"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [contributor(value=None, value_min=1, value_max=None)],
            now=NOW,
        )
    with pytest.raises(ValueError, match="must not exceed"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [contributor(value=None, value_min=3, value_max=2)],
            now=NOW,
        )


def test_timestamps_and_duplicates_fail_closed() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        calculate_activity_shadow(
            "presence_count", "observed", [contributor()], now=NOW.replace(tzinfo=None)
        )
    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [replace(contributor(), observed_at=NOW.replace(tzinfo=None))],
            now=NOW,
        )
    with pytest.raises(ValueError, match="fetched_at must be timezone-aware"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [replace(contributor(), fetched_at=NOW.replace(tzinfo=None))],
            now=NOW,
        )
    with pytest.raises(ValueError, match="after fetched_at"):
        calculate_activity_shadow(
            "presence_count",
            "observed",
            [replace(contributor(), observed_at=NOW, fetched_at=NOW - timedelta(minutes=1))],
            now=NOW,
        )
    with pytest.raises(ValueError, match="duplicate contributor_id"):
        calculate_activity_shadow(
            "presence_count", "observed", [contributor(), contributor()], now=NOW
        )


def test_runtime_literals_and_parameters_fail_closed() -> None:
    with pytest.raises(ValueError, match="unsupported observation_type"):
        calculate_activity_shadow("noise", "observed", [], now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported activity mode"):
        calculate_activity_shadow("proxy", "stale", [], now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="standardized_cap"):
        calculate_activity_shadow(
            "proxy", "unsupported", [], now=NOW, standardized_cap=0
        )


def test_result_dataclasses_are_frozen() -> None:
    result = calculate_activity_shadow(
        "presence_count", "observed", [contributor()], now=NOW
    )
    with pytest.raises(FrozenInstanceError):
        result.signal_mode = "forecast"  # type: ignore[misc]
