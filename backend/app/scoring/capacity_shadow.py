"""Pure offline venue-capacity pressure challenger.

The model combines one regional demand signal with one exactly matched venue
facility area through a dimensionless relative-size multiplier::

    pressure = demand * size_factor
    size_factor = (reference_area_m2 / effective_facility_area_m2) ** alpha
    effective_facility_area_m2 = max(facility_area_m2, area_floor_m2)

The size factor is dimensionless, so ``people_per_m2`` demand produces a
``people_per_m2`` structural pressure proxy and a dimensionless anomaly stays
dimensionless.  Catchment area remains provenance only and is deliberately not
used: assigning an entire hotspot population to every cafe would let catchment
geometry dominate.  This is not seat occupancy, capacity, probability, or a
public score.  Context and source quality remain auditable evidence; they never
become hidden multipliers.  The function performs no I/O or database/API work.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from app.config import (
    CAPACITY_SHADOW_AREA_FLOOR_M2,
    CAPACITY_SHADOW_MODEL_VERSION,
    CAPACITY_SHADOW_REFERENCE_AREA_M2,
    CAPACITY_SHADOW_SIZE_EXPONENT_ALPHA,
)


DemandUnit = Literal["people_per_m2", "dimensionless_anomaly"]
AreaUnit = Literal["m2"]
FacilityMatchStatus = Literal["verified", "ambiguous", "missing"]
VenueContext = Literal["standalone", "mall", "station", "takeout", "unknown"]


@dataclass(frozen=True, slots=True)
class AreaEvidence:
    """One area measurement with explicit unit and source quality."""

    value: float | None
    unit: AreaUnit | None
    unit_verified: bool
    quality: float
    provenance: str


@dataclass(frozen=True, slots=True)
class RegionalDemandEvidence:
    """Regional demand and the catchment support to which it applies."""

    value: float | None
    unit: DemandUnit | None
    unit_verified: bool
    quality: float
    source_id: str
    source_version: str
    provenance: str
    catchment_area: AreaEvidence


@dataclass(frozen=True, slots=True)
class VenueFacilityEvidence:
    """Exactly matched venue area plus non-numeric context evidence."""

    venue_id: str
    facility_area: AreaEvidence
    match_status: FacilityMatchStatus
    context: VenueContext
    context_quality: float
    source_id: str
    source_version: str
    context_provenance: str


@dataclass(frozen=True, slots=True)
class CapacityPressureEstimate:
    """Auditable structural pressure proxy; never a calibrated probability."""

    model_version: str
    demand: RegionalDemandEvidence
    venue: VenueFacilityEvidence
    pressure_value: float
    pressure_unit: DemandUnit
    size_factor: float
    reference_area_m2: float
    size_exponent_alpha: float
    effective_facility_area_m2: float
    facility_area_floor_m2: float
    facility_area_floor_applied: bool
    catchment_area_used_in_formula: Literal[False] = False
    calibrated_probability: None = None
    is_calibrated_probability: Literal[False] = False


def _validate_number(value: float, name: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isfinite(value):
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_quality(value: float, name: str) -> None:
    if isinstance(value, bool) or not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and between zero and one")


def _validate_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _validate_area(area: AreaEvidence, name: str) -> float:
    if area.unit_verified is not True:
        raise ValueError(f"{name} unit_verified must be true")
    if area.unit != "m2":
        raise ValueError(f"{name} unit must be verified m2")
    if area.value is None:
        raise ValueError(f"{name} value is required")
    _validate_number(area.value, f"{name} value", positive=True)
    _validate_quality(area.quality, f"{name} quality")
    _validate_text(area.provenance, f"{name} provenance")
    return area.value


def _validate_demand(demand: RegionalDemandEvidence) -> tuple[float, DemandUnit]:
    if demand.unit_verified is not True:
        raise ValueError("demand unit_verified must be true")
    allowed_units = ("people_per_m2", "dimensionless_anomaly")
    if demand.unit not in allowed_units:
        raise ValueError("demand unit must be people_per_m2 or dimensionless_anomaly")
    if demand.value is None:
        raise ValueError("demand value is required")
    _validate_number(demand.value, "demand value")
    if demand.unit == "people_per_m2" and demand.value < 0:
        raise ValueError("people_per_m2 demand must be non-negative")
    _validate_quality(demand.quality, "demand quality")
    for value, name in (
        (demand.source_id, "demand source_id"),
        (demand.source_version, "demand source_version"),
        (demand.provenance, "demand provenance"),
    ):
        _validate_text(value, name)
    return demand.value, demand.unit


def _validate_venue(venue: VenueFacilityEvidence) -> None:
    _validate_text(venue.venue_id, "venue_id")
    allowed_matches = ("verified", "ambiguous", "missing")
    if venue.match_status not in allowed_matches:
        raise ValueError("unsupported facility match_status")
    if venue.match_status != "verified":
        raise ValueError("facility match_status must be verified")
    allowed_contexts = ("standalone", "mall", "station", "takeout", "unknown")
    if venue.context not in allowed_contexts:
        raise ValueError("unsupported venue context")
    _validate_quality(venue.context_quality, "venue context_quality")
    for value, name in (
        (venue.source_id, "venue source_id"),
        (venue.source_version, "venue source_version"),
        (venue.context_provenance, "venue context_provenance"),
    ):
        _validate_text(value, name)


def calculate_capacity_shadow(
    demand: RegionalDemandEvidence,
    venue: VenueFacilityEvidence,
    *,
    reference_area_m2: float = CAPACITY_SHADOW_REFERENCE_AREA_M2,
    area_floor_m2: float = CAPACITY_SHADOW_AREA_FLOOR_M2,
    alpha: float = CAPACITY_SHADOW_SIZE_EXPONENT_ALPHA,
) -> CapacityPressureEstimate:
    """Return one deterministic, unit-consistent venue pressure estimate.

    Missing measurements, unverified units, and non-exact venue matches raise
    instead of producing a guessed value.  Mall/station/takeout/unknown context
    is preserved with its own quality and provenance but does not alter pressure.
    """

    _validate_number(reference_area_m2, "reference_area_m2", positive=True)
    _validate_number(area_floor_m2, "area_floor_m2", positive=True)
    if area_floor_m2 > reference_area_m2:
        raise ValueError("area_floor_m2 must not exceed reference_area_m2")
    _validate_number(alpha, "alpha", positive=True)
    if alpha > 1:
        raise ValueError("alpha must not exceed one")
    demand_value, demand_unit = _validate_demand(demand)
    # Required as auditable provenance, but intentionally absent from formula.
    _validate_area(demand.catchment_area, "catchment_area")
    _validate_venue(venue)
    facility_area_m2 = _validate_area(venue.facility_area, "facility_area")

    effective_facility_area_m2 = max(facility_area_m2, area_floor_m2)
    size_factor = (reference_area_m2 / effective_facility_area_m2) ** alpha
    pressure_value = demand_value * size_factor
    if not isfinite(size_factor) or not isfinite(pressure_value):
        raise ValueError("capacity pressure calculation must remain finite")

    return CapacityPressureEstimate(
        model_version=CAPACITY_SHADOW_MODEL_VERSION,
        demand=demand,
        venue=venue,
        pressure_value=pressure_value,
        pressure_unit=demand_unit,
        size_factor=size_factor,
        reference_area_m2=reference_area_m2,
        size_exponent_alpha=alpha,
        effective_facility_area_m2=effective_facility_area_m2,
        facility_area_floor_m2=area_floor_m2,
        facility_area_floor_applied=facility_area_m2 < area_floor_m2,
    )
