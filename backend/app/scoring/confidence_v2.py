"""Deterministic shadow calculation for decomposed input quality.

This module does not calculate an empirical probability that an estimate is
correct. ``input_quality`` only describes the strength and consistency of the
runtime evidence. Validation sufficiency is reported separately and is never
included in that score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp, sqrt
from typing import Literal, Sequence

from app.config import (
    CONF_V2_AGREEMENT_WEIGHT,
    CONF_V2_ALIGNMENT_TAU_MIN,
    CONF_V2_FRESHNESS_WEIGHT,
    CONF_V2_PARTIAL_CYCLE_FACTOR,
    CONF_V2_SINGLE_NEIGHBOR_AGREEMENT,
    CONF_V2_SOURCE_HEALTH_WEIGHT,
    CONF_V2_SPATIAL_WEIGHT,
    CONF_V2_VALIDATION_TARGET_SAMPLES,
    FRESHNESS_MAX_FUTURE_SKEW_MIN,
    R_MAX_M,
    TAU_MIN,
)


CycleStatus = Literal["complete", "partial", "failed", "unavailable"]
ValidationStatus = Literal["not_evaluated", "insufficient", "sufficient"]


@dataclass(frozen=True, slots=True)
class ConfidenceContributor:
    """One selected hotspot and its normalized or raw spatial weight."""

    distance_m: float
    level: int
    observed_at: datetime
    weight: float
    inside_polygon: bool = False


@dataclass(frozen=True, slots=True)
class CycleEvidence:
    """Health of the completed source cycle that produced the evidence."""

    status: CycleStatus
    target_count: int
    saved_count: int
    failed_count: int


@dataclass(frozen=True, slots=True)
class ContributorFreshness:
    distance_m: float
    age_minutes: float
    normalized_weight: float
    score: float


@dataclass(frozen=True, slots=True)
class ConfidenceV2Components:
    spatial_fit: float
    freshness: float
    neighbor_agreement: float
    level_agreement: float
    temporal_alignment: float
    source_cycle_health: float
    validation_sufficiency: float | None


@dataclass(frozen=True, slots=True)
class ConfidenceV2Result:
    """Shadow evidence-quality result; never an accuracy probability."""

    input_quality: float
    components: ConfidenceV2Components
    contributor_freshness: tuple[ContributorFreshness, ...]
    validation_status: ValidationStatus
    calibrated_probability: None = None
    is_calibrated_probability: Literal[False] = False


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _validate_parameters(
    *,
    r_max_m: float,
    freshness_tau_min: float,
    alignment_tau_min: float,
    single_neighbor_agreement: float,
    partial_cycle_factor: float,
    component_weights: tuple[float, float, float, float],
    validation_target_samples: int,
    max_future_skew_min: float,
) -> None:
    if r_max_m <= 0:
        raise ValueError("r_max_m must be positive")
    if freshness_tau_min <= 0:
        raise ValueError("freshness_tau_min must be positive")
    if alignment_tau_min <= 0:
        raise ValueError("alignment_tau_min must be positive")
    if not 0 <= single_neighbor_agreement <= 1:
        raise ValueError("single_neighbor_agreement must be between zero and one")
    if not 0 <= partial_cycle_factor <= 1:
        raise ValueError("partial_cycle_factor must be between zero and one")
    if any(weight < 0 for weight in component_weights) or sum(component_weights) <= 0:
        raise ValueError("component weights must be non-negative with a positive sum")
    if validation_target_samples < 1:
        raise ValueError("validation_target_samples must be positive")
    if max_future_skew_min < 0:
        raise ValueError("max_future_skew_min must be non-negative")


def _cycle_health(
    evidence: CycleEvidence,
    *,
    partial_cycle_factor: float,
) -> float:
    if evidence.target_count < 0 or evidence.saved_count < 0 or evidence.failed_count < 0:
        raise ValueError("cycle counts must be non-negative")
    if evidence.saved_count + evidence.failed_count > evidence.target_count:
        raise ValueError("saved_count + failed_count cannot exceed target_count")
    if evidence.status == "complete" and not (
        evidence.target_count > 0
        and evidence.saved_count == evidence.target_count
        and evidence.failed_count == 0
    ):
        raise ValueError("complete cycle must save every target without failures")
    if evidence.target_count == 0:
        if evidence.status == "unavailable" and not (
            evidence.saved_count or evidence.failed_count
        ):
            return 0.0
        raise ValueError("target_count must be positive for an available cycle")

    success_ratio = evidence.saved_count / evidence.target_count
    status_factor = {
        "complete": 1.0,
        "partial": partial_cycle_factor,
        "failed": 0.0,
        "unavailable": 0.0,
    }.get(evidence.status)
    if status_factor is None:
        raise ValueError("unsupported cycle status")
    return _clamp_unit(success_ratio * status_factor)


def calculate_confidence_v2(
    contributors: Sequence[ConfidenceContributor],
    *,
    now: datetime,
    cycle: CycleEvidence,
    validation_sample_count: int | None = None,
    r_max_m: float = R_MAX_M,
    freshness_tau_min: float = TAU_MIN,
    alignment_tau_min: float = CONF_V2_ALIGNMENT_TAU_MIN,
    single_neighbor_agreement: float = CONF_V2_SINGLE_NEIGHBOR_AGREEMENT,
    partial_cycle_factor: float = CONF_V2_PARTIAL_CYCLE_FACTOR,
    spatial_weight: float = CONF_V2_SPATIAL_WEIGHT,
    freshness_weight: float = CONF_V2_FRESHNESS_WEIGHT,
    agreement_weight: float = CONF_V2_AGREEMENT_WEIGHT,
    source_health_weight: float = CONF_V2_SOURCE_HEALTH_WEIGHT,
    validation_target_samples: int = CONF_V2_VALIDATION_TARGET_SAMPLES,
    max_future_skew_min: float = FRESHNESS_MAX_FUTURE_SKEW_MIN,
) -> ConfidenceV2Result | None:
    """Return decomposed evidence quality, or ``None`` without contributors.

    Weights need not be pre-normalized. Validation sufficiency describes only
    sample quantity and is deliberately excluded from ``input_quality``.
    """

    component_weights = (
        spatial_weight,
        freshness_weight,
        agreement_weight,
        source_health_weight,
    )
    _validate_parameters(
        r_max_m=r_max_m,
        freshness_tau_min=freshness_tau_min,
        alignment_tau_min=alignment_tau_min,
        single_neighbor_agreement=single_neighbor_agreement,
        partial_cycle_factor=partial_cycle_factor,
        component_weights=component_weights,
        validation_target_samples=validation_target_samples,
        max_future_skew_min=max_future_skew_min,
    )
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not contributors:
        return None

    for contributor in contributors:
        if contributor.distance_m < 0:
            raise ValueError("contributor distance_m must be non-negative")
        if contributor.distance_m > r_max_m:
            raise ValueError("contributor distance_m cannot exceed r_max_m")
        if contributor.level not in (1, 2, 3, 4):
            raise ValueError("contributor level must be between 1 and 4")
        if contributor.observed_at.tzinfo is None:
            raise ValueError("contributor observed_at must be timezone-aware")
        if contributor.weight <= 0:
            raise ValueError("contributor weight must be positive")

    raw_weight_sum = sum(item.weight for item in contributors)
    normalized_weights = tuple(item.weight / raw_weight_sum for item in contributors)
    ages: list[float] = []
    freshness_details: list[ContributorFreshness] = []
    for contributor, normalized_weight in zip(
        contributors, normalized_weights, strict=True
    ):
        signed_age = (now - contributor.observed_at).total_seconds() / 60.0
        if signed_age < -max_future_skew_min:
            raise ValueError("contributor observed_at exceeds allowed future skew")
        age_minutes = max(0.0, signed_age)
        ages.append(age_minutes)
        freshness_score = exp(-age_minutes / freshness_tau_min)
        freshness_details.append(
            ContributorFreshness(
                distance_m=contributor.distance_m,
                age_minutes=age_minutes,
                normalized_weight=normalized_weight,
                score=freshness_score,
            )
        )

    # A polygon challenger can mark containment explicitly and pass boundary
    # distance otherwise. Point-IDW callers leave ``inside_polygon`` false and
    # pass representative-point distance, preserving a common interface.
    spatial_fit = (
        1.0
        if any(item.inside_polygon for item in contributors)
        else _clamp_unit(1.0 - min(item.distance_m for item in contributors) / r_max_m)
    )
    freshness = sum(
        item.normalized_weight * item.score for item in freshness_details
    )

    if len(contributors) == 1:
        level_agreement = single_neighbor_agreement
        temporal_alignment = 1.0
        neighbor_agreement = single_neighbor_agreement
    else:
        weighted_level = sum(
            weight * contributor.level
            for contributor, weight in zip(
                contributors, normalized_weights, strict=True
            )
        )
        weighted_variance = sum(
            weight * (contributor.level - weighted_level) ** 2
            for contributor, weight in zip(
                contributors, normalized_weights, strict=True
            )
        )
        # 1.5 is the maximum weighted standard deviation on the 1..4 scale.
        level_agreement = _clamp_unit(1.0 - sqrt(weighted_variance) / 1.5)
        timestamp_span_min = (
            max(item.observed_at for item in contributors)
            - min(item.observed_at for item in contributors)
        ).total_seconds() / 60.0
        temporal_alignment = exp(-timestamp_span_min / alignment_tau_min)
        neighbor_agreement = level_agreement * temporal_alignment

    source_cycle_health = _cycle_health(
        cycle,
        partial_cycle_factor=partial_cycle_factor,
    )

    if validation_sample_count is None:
        validation_sufficiency = None
        validation_status: ValidationStatus = "not_evaluated"
    else:
        if validation_sample_count < 0:
            raise ValueError("validation_sample_count must be non-negative")
        validation_sufficiency = _clamp_unit(
            validation_sample_count / validation_target_samples
        )
        validation_status = (
            "sufficient"
            if validation_sample_count >= validation_target_samples
            else "insufficient"
        )

    weighted_sum = (
        spatial_fit * spatial_weight
        + freshness * freshness_weight
        + neighbor_agreement * agreement_weight
        + source_cycle_health * source_health_weight
    )
    input_quality = _clamp_unit(weighted_sum / sum(component_weights))

    return ConfidenceV2Result(
        input_quality=input_quality,
        components=ConfidenceV2Components(
            spatial_fit=spatial_fit,
            freshness=freshness,
            neighbor_agreement=neighbor_agreement,
            level_agreement=level_agreement,
            temporal_alignment=temporal_alignment,
            source_cycle_health=source_cycle_health,
            validation_sufficiency=validation_sufficiency,
        ),
        contributor_freshness=tuple(freshness_details),
        validation_status=validation_status,
    )
