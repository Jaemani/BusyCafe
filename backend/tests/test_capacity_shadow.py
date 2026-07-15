from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from math import inf, nan

import pytest

from app.config import (
    CAPACITY_SHADOW_AREA_FLOOR_M2,
    CAPACITY_SHADOW_MODEL_VERSION,
    CAPACITY_SHADOW_REFERENCE_AREA_M2,
    CAPACITY_SHADOW_SIZE_EXPONENT_ALPHA,
    CAPACITY_SHADOW_SIZE_EXPONENT_GRID,
)
from app.scoring.capacity_shadow import (
    AreaEvidence,
    RegionalDemandEvidence,
    VenueFacilityEvidence,
    calculate_capacity_shadow,
)


def area(
    value: float | None,
    *,
    unit: str | None = "m2",
    unit_verified: bool = True,
    quality: float = 0.9,
    provenance: str = "fixture:area",
) -> AreaEvidence:
    return AreaEvidence(
        value=value,
        unit=unit,  # type: ignore[arg-type]
        unit_verified=unit_verified,
        quality=quality,
        provenance=provenance,
    )


def demand(
    *,
    value: float | None = 0.5,
    unit: str | None = "people_per_m2",
    unit_verified: bool = True,
    catchment_area_m2: float | None = 1_000.0,
    catchment_unit_verified: bool = True,
    quality: float = 0.8,
) -> RegionalDemandEvidence:
    return RegionalDemandEvidence(
        value=value,
        unit=unit,  # type: ignore[arg-type]
        unit_verified=unit_verified,
        quality=quality,
        source_id="seoul-density-shadow",
        source_version="fixture-demand-v1",
        provenance="fixture:demand",
        catchment_area=area(
            catchment_area_m2,
            unit_verified=catchment_unit_verified,
            quality=0.7,
            provenance="fixture:catchment",
        ),
    )


def venue(
    *,
    facility_area_m2: float | None = 100.0,
    facility_unit_verified: bool = True,
    match_status: str = "verified",
    context: str = "standalone",
    context_quality: float = 0.75,
) -> VenueFacilityEvidence:
    return VenueFacilityEvidence(
        venue_id="cafe-1",
        facility_area=area(
            facility_area_m2,
            unit_verified=facility_unit_verified,
            quality=0.6,
            provenance="fixture:facility-area",
        ),
        match_status=match_status,  # type: ignore[arg-type]
        context=context,  # type: ignore[arg-type]
        context_quality=context_quality,
        source_id="verified-venue-register",
        source_version="fixture-venue-v1",
        context_provenance="fixture:venue-context",
    )


def test_people_density_combines_with_relative_size_factor_dimensionally() -> None:
    result = calculate_capacity_shadow(demand(), venue())
    expected_factor = (CAPACITY_SHADOW_REFERENCE_AREA_M2 / 100.0) ** 0.5

    assert result.model_version == CAPACITY_SHADOW_MODEL_VERSION
    assert result.pressure_value == pytest.approx(0.5 * expected_factor)
    assert result.pressure_unit == "people_per_m2"
    assert result.size_factor == pytest.approx(expected_factor)
    assert result.reference_area_m2 == CAPACITY_SHADOW_REFERENCE_AREA_M2
    assert result.size_exponent_alpha == CAPACITY_SHADOW_SIZE_EXPONENT_ALPHA
    assert result.catchment_area_used_in_formula is False
    assert result.effective_facility_area_m2 == 100.0
    assert result.facility_area_floor_applied is False
    assert result.calibrated_probability is None
    assert result.is_calibrated_probability is False


def test_dimensionless_anomaly_remains_dimensionless_and_may_be_signed() -> None:
    positive = calculate_capacity_shadow(
        demand(value=1.2, unit="dimensionless_anomaly"), venue()
    )
    negative = calculate_capacity_shadow(
        demand(value=-0.2, unit="dimensionless_anomaly"), venue()
    )

    expected_factor = (CAPACITY_SHADOW_REFERENCE_AREA_M2 / 100.0) ** 0.5
    assert positive.pressure_value == pytest.approx(1.2 * expected_factor)
    assert negative.pressure_value == pytest.approx(-0.2 * expected_factor)
    assert positive.pressure_unit == negative.pressure_unit == "dimensionless_anomaly"


def test_same_positive_demand_gives_smaller_venue_higher_pressure() -> None:
    smaller = calculate_capacity_shadow(demand(), venue(facility_area_m2=50.0))
    larger = calculate_capacity_shadow(demand(), venue(facility_area_m2=200.0))

    assert smaller.pressure_value > larger.pressure_value
    assert smaller.pressure_value == pytest.approx(2 * larger.pressure_value)


def test_area_floor_caps_tiny_area_extremes_monotonically() -> None:
    at_floor = calculate_capacity_shadow(
        demand(), venue(facility_area_m2=CAPACITY_SHADOW_AREA_FLOOR_M2)
    )
    below_floor = calculate_capacity_shadow(demand(), venue(facility_area_m2=5.0))
    tiny = calculate_capacity_shadow(demand(), venue(facility_area_m2=0.001))
    above_floor = calculate_capacity_shadow(demand(), venue(facility_area_m2=20.0))

    assert tiny.pressure_value == below_floor.pressure_value == at_floor.pressure_value
    assert at_floor.pressure_value > above_floor.pressure_value
    assert tiny.effective_facility_area_m2 == CAPACITY_SHADOW_AREA_FLOOR_M2
    assert tiny.facility_area_floor_applied is True


def test_reference_facility_and_floor_common_scale_is_invariant() -> None:
    base = calculate_capacity_shadow(
        demand(),
        venue(facility_area_m2=100),
        reference_area_m2=42.9,
        area_floor_m2=10,
    )
    scaled = calculate_capacity_shadow(
        demand(),
        venue(facility_area_m2=1_000),
        reference_area_m2=429,
        area_floor_m2=100,
    )

    assert scaled.size_factor == pytest.approx(base.size_factor)
    assert scaled.pressure_value == pytest.approx(base.pressure_value)


def test_catchment_area_is_validated_provenance_but_not_formula_input() -> None:
    small = calculate_capacity_shadow(demand(catchment_area_m2=100), venue())
    huge = calculate_capacity_shadow(demand(catchment_area_m2=1_000_000), venue())

    assert small.pressure_value == huge.pressure_value
    assert small.catchment_area_used_in_formula is False
    assert small.demand.catchment_area.value == 100
    assert huge.demand.catchment_area.value == 1_000_000


def test_preregistered_defaults_and_alpha_grid_are_fixed() -> None:
    assert CAPACITY_SHADOW_REFERENCE_AREA_M2 == 42.9
    assert CAPACITY_SHADOW_AREA_FLOOR_M2 == 10.0
    assert CAPACITY_SHADOW_SIZE_EXPONENT_ALPHA == 0.5
    assert CAPACITY_SHADOW_SIZE_EXPONENT_GRID == (0.25, 0.5, 0.75, 1.0)


@pytest.mark.parametrize(
    ("bad_demand", "bad_venue", "message"),
    [
        (demand(unit_verified=False), venue(), "demand unit_verified"),
        (
            demand(catchment_unit_verified=False),
            venue(),
            "catchment_area unit_verified",
        ),
        (
            demand(),
            venue(facility_unit_verified=False),
            "facility_area unit_verified",
        ),
    ],
)
def test_any_false_unit_verified_flag_refuses_calculation(
    bad_demand: RegionalDemandEvidence,
    bad_venue: VenueFacilityEvidence,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_capacity_shadow(bad_demand, bad_venue)


@pytest.mark.parametrize(
    ("bad_demand", "bad_venue", "message"),
    [
        (demand(value=None), venue(), "demand value is required"),
        (demand(unit=None), venue(), "demand unit"),
        (demand(catchment_area_m2=None), venue(), "catchment_area value is required"),
        (demand(), venue(facility_area_m2=None), "facility_area value is required"),
        (replace(demand(), catchment_area=area(100, unit=None)), venue(), "unit"),
        (
            demand(),
            replace(venue(), facility_area=area(100, unit=None)),
            "unit",
        ),
    ],
)
def test_missing_value_or_unit_fails_closed(
    bad_demand: RegionalDemandEvidence,
    bad_venue: VenueFacilityEvidence,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_capacity_shadow(bad_demand, bad_venue)


@pytest.mark.parametrize("match_status", ["ambiguous", "missing"])
def test_non_verified_facility_match_fails_closed(match_status: str) -> None:
    with pytest.raises(ValueError, match="match_status must be verified"):
        calculate_capacity_shadow(demand(), venue(match_status=match_status))


@pytest.mark.parametrize("context", ["mall", "station", "takeout", "unknown"])
def test_uncertain_context_stays_in_quality_and_provenance_not_pressure(
    context: str,
) -> None:
    contextual = calculate_capacity_shadow(
        demand(), venue(context=context, context_quality=0.2)
    )
    standalone = calculate_capacity_shadow(demand(), venue())

    assert contextual.pressure_value == standalone.pressure_value
    assert contextual.venue.context == context
    assert contextual.venue.context_quality == 0.2
    assert contextual.venue.context_provenance == "fixture:venue-context"
    assert contextual.venue.facility_area.quality == 0.6


@pytest.mark.parametrize(
    ("bad_demand", "bad_venue", "floor", "message"),
    [
        (demand(value=-0.1), venue(), 20.0, "must be non-negative"),
        (demand(value=nan), venue(), 20.0, "must be finite"),
        (demand(), venue(facility_area_m2=0), 20.0, "must be positive"),
        (demand(catchment_area_m2=inf), venue(), 20.0, "must be finite"),
        (replace(demand(), quality=1.1), venue(), 20.0, "demand quality"),
        (demand(), venue(context_quality=-0.1), 20.0, "context_quality"),
        (demand(), venue(), 0.0, "area_floor_m2 must be positive"),
    ],
)
def test_invalid_numeric_inputs_fail_closed(
    bad_demand: RegionalDemandEvidence,
    bad_venue: VenueFacilityEvidence,
    floor: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_capacity_shadow(bad_demand, bad_venue, area_floor_m2=floor)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"reference_area_m2": 0.0}, "reference_area_m2 must be positive"),
        ({"reference_area_m2": nan}, "reference_area_m2 must be finite"),
        ({"area_floor_m2": 0.0}, "area_floor_m2 must be positive"),
        (
            {"reference_area_m2": 10.0, "area_floor_m2": 20.0},
            "must not exceed reference_area_m2",
        ),
        ({"alpha": 0.0}, "alpha must be positive"),
        ({"alpha": 1.01}, "alpha must not exceed one"),
        ({"alpha": nan}, "alpha must be finite"),
    ],
)
def test_invalid_relative_size_parameters_fail_closed(
    kwargs: dict[str, float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_capacity_shadow(demand(), venue(), **kwargs)


def test_result_is_deterministic_and_all_contracts_are_frozen() -> None:
    demand_input = demand()
    venue_input = venue(context="unknown", context_quality=0.3)

    first = calculate_capacity_shadow(demand_input, venue_input)
    second = calculate_capacity_shadow(demand_input, venue_input)

    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.pressure_value = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        demand_input.value = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        venue_input.facility_area.value = 0.0  # type: ignore[misc]
