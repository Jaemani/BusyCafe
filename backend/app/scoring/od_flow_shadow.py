"""Deterministic destination/hour aggregates for historical OD research.

This module is shadow-only and performs no I/O.  It turns purpose-labelled
origin/destination rows into zone-by-hour evidence.  Counts are kept in their
source unit and are not promoted to a public crowd score.

The direction vector is the flow-weighted circular mean of unit bearings from
origin centroids to the destination centroid.  Intrazonal flow contributes to
all count totals but not to direction.  Missing or coincident centroids reduce
``direction_coverage`` instead of inventing a bearing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from math import (
    atan2,
    cos,
    degrees,
    hypot,
    isfinite,
    radians,
    sin,
    ulp,
)


_VECTOR_ZERO_TOLERANCE = 32.0 * ulp(1.0)


@dataclass(frozen=True, slots=True)
class ODFlowObservation:
    origin_id: str
    destination_id: str
    departure_hour: int
    arrival_hour: int
    purpose: str
    flow: Decimal | float | int


@dataclass(frozen=True, slots=True)
class ODZoneCentroid:
    zone_id: str
    lat: float
    lng: float


@dataclass(frozen=True, slots=True)
class ODPurposeAggregate:
    purpose: str
    total: float
    ratio: float


@dataclass(frozen=True, slots=True)
class ODZoneHourAggregate:
    zone_id: str
    hour: int
    inbound: float
    outbound: float
    net: float
    intrazonal_inbound: float
    intrazonal_outbound: float
    purposes: tuple[ODPurposeAggregate, ...]
    # Mean unit-vector components: east and north, respectively.  Components
    # remain useful when the mean cancels to zero, while bearing is then absent.
    direction_east: float | None
    direction_north: float | None
    direction_bearing_deg: float | None
    direction_strength: float | None
    # Fraction of positive, non-intrazonal inbound flow with usable origin and
    # destination centroids.  Intrazonal flow is outside this denominator.
    direction_coverage: float


def _require_canonical_text(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")


def _validate_observation(observation: ODFlowObservation) -> None:
    _require_canonical_text(observation.origin_id, "origin_id")
    _require_canonical_text(observation.destination_id, "destination_id")
    _require_canonical_text(observation.purpose, "purpose")
    for value, name in (
        (observation.departure_hour, "departure_hour"),
        (observation.arrival_hour, "arrival_hour"),
    ):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 23
        ):
            raise ValueError(f"{name} must be an integer in 0..23")
    if isinstance(observation.flow, bool) or not isinstance(
        observation.flow, (Decimal, int, float)
    ):
        raise ValueError("flow must be finite and non-negative")
    if isinstance(observation.flow, Decimal):
        valid_flow = observation.flow.is_finite() and observation.flow >= 0
    else:
        valid_flow = isfinite(observation.flow) and observation.flow >= 0
    if not valid_flow:
        raise ValueError("flow must be finite and non-negative")


def _decimal_flow(value: Decimal | float | int) -> Decimal:
    """Convert source estimates to an order-independent exact accumulator."""

    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _validate_centroid(centroid: ODZoneCentroid) -> None:
    _require_canonical_text(centroid.zone_id, "zone_id")
    if not isfinite(centroid.lat) or not -90.0 <= centroid.lat <= 90.0:
        raise ValueError("centroid lat must be finite and in [-90, 90]")
    if not isfinite(centroid.lng) or not -180.0 <= centroid.lng <= 180.0:
        raise ValueError("centroid lng must be finite and in [-180, 180]")


def _unit_bearing(
    origin: ODZoneCentroid,
    destination: ODZoneCentroid,
) -> tuple[float, float] | None:
    """Return spherical initial-bearing components as ``(east, north)``."""

    origin_lat = radians(origin.lat)
    destination_lat = radians(destination.lat)
    delta_lng = radians(destination.lng - origin.lng)
    east = sin(delta_lng) * cos(destination_lat)
    north = (
        cos(origin_lat) * sin(destination_lat)
        - sin(origin_lat) * cos(destination_lat) * cos(delta_lng)
    )
    magnitude = hypot(east, north)
    if magnitude <= _VECTOR_ZERO_TOLERANCE:
        return None
    return east / magnitude, north / magnitude


def aggregate_od_flow_shadow(
    observations: Iterable[ODFlowObservation],
    centroids: Iterable[ODZoneCentroid],
) -> tuple[ODZoneHourAggregate, ...]:
    """Aggregate OD rows without I/O or dependence on caller ordering.

    One result is emitted for the union of positive-flow destination/arrival
    and origin/departure keys.  Inbound and its purpose/direction evidence use
    ``arrival_hour``; outbound uses ``departure_hour``.  Net flow therefore
    compares aligned zone/hour events rather than two arrival-time series.
    """

    centroid_by_zone: dict[str, ODZoneCentroid] = {}
    for centroid in centroids:
        _validate_centroid(centroid)
        if centroid.zone_id in centroid_by_zone:
            raise ValueError(f"duplicate centroid zone_id: {centroid.zone_id}")
        centroid_by_zone[centroid.zone_id] = centroid

    inbound_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    outbound_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    intrazonal_inbound_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    intrazonal_outbound_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    purpose_values: dict[tuple[str, int, str], Decimal] = defaultdict(Decimal)
    purpose_names: dict[tuple[str, int], set[str]] = defaultdict(set)
    eligible_direction_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    covered_direction_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    east_direction_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    north_direction_values: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    direction_cache: dict[tuple[str, str], tuple[float, float] | None] = {}
    result_keys: set[tuple[str, int]] = set()

    for observation in observations:
        _validate_observation(observation)
        flow = _decimal_flow(observation.flow)
        if flow == 0:
            continue
        inbound_key = (observation.destination_id, observation.arrival_hour)
        outbound_key = (observation.origin_id, observation.departure_hour)
        result_keys.update((inbound_key, outbound_key))
        inbound_values[inbound_key] += flow
        outbound_values[outbound_key] += flow
        purpose_values[(*inbound_key, observation.purpose)] += flow
        purpose_names[inbound_key].add(observation.purpose)
        if observation.origin_id == observation.destination_id:
            intrazonal_inbound_values[inbound_key] += flow
            intrazonal_outbound_values[outbound_key] += flow
            continue

        eligible_direction_values[inbound_key] += flow
        direction_key = (observation.origin_id, observation.destination_id)
        if direction_key not in direction_cache:
            origin = centroid_by_zone.get(observation.origin_id)
            destination = centroid_by_zone.get(observation.destination_id)
            direction_cache[direction_key] = (
                _unit_bearing(origin, destination)
                if origin is not None and destination is not None
                else None
            )
        unit = direction_cache[direction_key]
        if unit is None:
            continue
        covered_direction_values[inbound_key] += flow
        east_direction_values[inbound_key] += flow * Decimal(str(unit[0]))
        north_direction_values[inbound_key] += flow * Decimal(str(unit[1]))

    results: list[ODZoneHourAggregate] = []
    for zone_id, hour in sorted(result_keys):
        key = (zone_id, hour)
        inbound_decimal = inbound_values[key]
        outbound_decimal = outbound_values[key]
        inbound = float(inbound_decimal)
        outbound = float(outbound_decimal)
        intrazonal_inbound = float(intrazonal_inbound_values[key])
        intrazonal_outbound = float(intrazonal_outbound_values[key])
        purposes_for_key = sorted(purpose_names[key])
        purposes = tuple(
            ODPurposeAggregate(
                purpose=purpose,
                total=float(total := purpose_values[(*key, purpose)]),
                ratio=float(total / inbound_decimal),
            )
            for purpose in purposes_for_key
        )

        eligible_flow = eligible_direction_values[key]
        covered_flow = covered_direction_values[key]
        direction_coverage = (
            min(1.0, float(covered_flow / eligible_flow))
            if eligible_flow > 0
            else 0.0
        )
        if covered_flow > 0:
            direction_east = float(east_direction_values[key] / covered_flow)
            direction_north = float(north_direction_values[key] / covered_flow)
            direction_strength = min(1.0, hypot(direction_east, direction_north))
            if direction_strength <= _VECTOR_ZERO_TOLERANCE:
                direction_bearing_deg = None
            else:
                direction_bearing_deg = (
                    degrees(atan2(direction_east, direction_north)) + 360.0
                ) % 360.0
        else:
            direction_east = None
            direction_north = None
            direction_bearing_deg = None
            direction_strength = None

        results.append(
            ODZoneHourAggregate(
                zone_id=zone_id,
                hour=hour,
                inbound=inbound,
                outbound=outbound,
                net=inbound - outbound,
                intrazonal_inbound=intrazonal_inbound,
                intrazonal_outbound=intrazonal_outbound,
                purposes=purposes,
                direction_east=direction_east,
                direction_north=direction_north,
                direction_bearing_deg=direction_bearing_deg,
                direction_strength=direction_strength,
                direction_coverage=direction_coverage,
            )
        )
    return tuple(results)
