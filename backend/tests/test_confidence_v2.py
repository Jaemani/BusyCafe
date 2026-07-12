from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.scoring.confidence_v2 import (
    ConfidenceContributor,
    CycleEvidence,
    calculate_confidence_v2,
)


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
HEALTHY_CYCLE = CycleEvidence(
    status="complete",
    target_count=121,
    saved_count=121,
    failed_count=0,
)


def contributor(
    *,
    distance_m: float = 100,
    level: int = 2,
    age_minutes: float = 0,
    weight: float = 1,
) -> ConfidenceContributor:
    return ConfidenceContributor(
        distance_m=distance_m,
        level=level,
        observed_at=NOW - timedelta(minutes=age_minutes),
        weight=weight,
    )


def test_returns_none_without_evidence() -> None:
    assert calculate_confidence_v2([], now=NOW, cycle=HEALTHY_CYCLE) is None


def test_exposes_deterministic_components_without_probability_claim() -> None:
    evidence = [
        contributor(distance_m=150, level=2, age_minutes=0, weight=3),
        contributor(distance_m=300, level=2, age_minutes=15, weight=1),
    ]

    first = calculate_confidence_v2(
        evidence,
        now=NOW,
        cycle=HEALTHY_CYCLE,
        r_max_m=1_500,
    )
    second = calculate_confidence_v2(
        evidence,
        now=NOW,
        cycle=HEALTHY_CYCLE,
        r_max_m=1_500,
    )

    assert first == second
    assert first is not None
    assert first.is_calibrated_probability is False
    assert first.calibrated_probability is None
    assert first.components.spatial_fit == pytest.approx(0.9)
    assert first.components.freshness == pytest.approx(0.75 + 0.25 / 2.718281828)
    assert first.components.level_agreement == pytest.approx(1.0)
    assert first.components.temporal_alignment == pytest.approx(0.22313016)
    assert first.components.neighbor_agreement == pytest.approx(0.22313016)
    assert first.components.source_cycle_health == pytest.approx(1.0)
    assert 0 < first.input_quality < 1
    assert [item.normalized_weight for item in first.contributor_freshness] == pytest.approx(
        [0.75, 0.25]
    )


def test_polygon_containment_is_supported_without_a_separate_calculator() -> None:
    result = calculate_confidence_v2(
        [
            ConfidenceContributor(
                distance_m=250,
                level=2,
                observed_at=NOW,
                weight=1,
                inside_polygon=True,
            )
        ],
        now=NOW,
        cycle=HEALTHY_CYCLE,
    )

    assert result is not None
    assert result.components.spatial_fit == pytest.approx(1.0)


def test_disagreement_and_timestamp_skew_reduce_agreement() -> None:
    aligned = calculate_confidence_v2(
        [contributor(level=2), contributor(level=2)],
        now=NOW,
        cycle=HEALTHY_CYCLE,
    )
    divergent = calculate_confidence_v2(
        [contributor(level=1), contributor(level=4, age_minutes=30)],
        now=NOW,
        cycle=HEALTHY_CYCLE,
    )

    assert aligned is not None and divergent is not None
    assert aligned.components.neighbor_agreement == pytest.approx(1.0)
    assert divergent.components.level_agreement == pytest.approx(0.0)
    assert divergent.components.neighbor_agreement == pytest.approx(0.0)
    assert divergent.input_quality < aligned.input_quality


def test_single_neighbor_is_explicitly_not_full_agreement() -> None:
    result = calculate_confidence_v2(
        [contributor()],
        now=NOW,
        cycle=HEALTHY_CYCLE,
        single_neighbor_agreement=0.4,
    )

    assert result is not None
    assert result.components.level_agreement == pytest.approx(0.4)
    assert result.components.temporal_alignment == pytest.approx(1.0)
    assert result.components.neighbor_agreement == pytest.approx(0.4)


def test_cycle_failure_and_partial_cycle_are_fail_closed() -> None:
    partial = calculate_confidence_v2(
        [contributor()],
        now=NOW,
        cycle=CycleEvidence("partial", 100, 80, 20),
        partial_cycle_factor=0.5,
    )
    failed = calculate_confidence_v2(
        [contributor()],
        now=NOW,
        cycle=CycleEvidence("failed", 100, 80, 20),
    )

    assert partial is not None and failed is not None
    assert partial.components.source_cycle_health == pytest.approx(0.4)
    assert failed.components.source_cycle_health == pytest.approx(0.0)
    assert failed.input_quality < partial.input_quality


def test_validation_sufficiency_is_separate_from_input_quality() -> None:
    kwargs = {"now": NOW, "cycle": HEALTHY_CYCLE, "validation_target_samples": 100}
    unknown = calculate_confidence_v2([contributor()], **kwargs)
    insufficient = calculate_confidence_v2(
        [contributor()], validation_sample_count=25, **kwargs
    )
    sufficient = calculate_confidence_v2(
        [contributor()], validation_sample_count=100, **kwargs
    )

    assert unknown is not None and insufficient is not None and sufficient is not None
    assert unknown.validation_status == "not_evaluated"
    assert unknown.components.validation_sufficiency is None
    assert insufficient.validation_status == "insufficient"
    assert insufficient.components.validation_sufficiency == pytest.approx(0.25)
    assert sufficient.validation_status == "sufficient"
    assert sufficient.components.validation_sufficiency == pytest.approx(1.0)
    assert unknown.input_quality == insufficient.input_quality == sufficient.input_quality


@pytest.mark.parametrize(
    ("contributors", "cycle", "kwargs", "message"),
    [
        ([contributor(distance_m=-1)], HEALTHY_CYCLE, {}, "distance_m"),
        ([contributor(distance_m=1_501)], HEALTHY_CYCLE, {}, "r_max_m"),
        ([contributor(level=5)], HEALTHY_CYCLE, {}, "level"),
        ([contributor(weight=0)], HEALTHY_CYCLE, {}, "weight"),
        (
            [contributor()],
            CycleEvidence("complete", 100, 90, 20),
            {},
            "cannot exceed",
        ),
        (
            [contributor()],
            CycleEvidence("complete", 100, 90, 0),
            {},
            "save every target",
        ),
        ([contributor()], HEALTHY_CYCLE, {"freshness_tau_min": 0}, "freshness"),
        ([contributor()], HEALTHY_CYCLE, {"validation_sample_count": -1}, "validation"),
    ],
)
def test_invalid_inputs_fail_closed(
    contributors: list[ConfidenceContributor],
    cycle: CycleEvidence,
    kwargs: dict[str, float],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_confidence_v2(contributors, now=NOW, cycle=cycle, **kwargs)


def test_naive_and_excessively_future_timestamps_are_rejected() -> None:
    naive = ConfidenceContributor(100, 2, NOW.replace(tzinfo=None), 1)
    future = ConfidenceContributor(100, 2, NOW + timedelta(minutes=3), 1)

    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_confidence_v2([naive], now=NOW, cycle=HEALTHY_CYCLE)
    with pytest.raises(ValueError, match="future skew"):
        calculate_confidence_v2([future], now=NOW, cycle=HEALTHY_CYCLE)
