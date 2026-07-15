"""Pure conservative entity resolution between permits and cafe places.

The sources share no identifier.  A cafe is therefore a strong candidate only
when normalized address is exactly equal, WGS84 distance is within the fixed
gate, and either normalized name or normalized phone is exactly equal.  One
strong candidate verifies; zero is missing; multiple candidates are ambiguous
and explicitly abstain.  No fuzzy matching, I/O, database write, or public
catalog mutation occurs here.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from math import isfinite
from typing import Literal, Sequence

from app.config import (
    PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
    PERMIT_CAFE_ENTITY_MIN_PHONE_DIGITS,
)
from app.geo import haversine_m


CoordinateUnit = Literal["wgs84_degrees"]
ResolutionStatus = Literal["verified", "missing", "ambiguous"]


@dataclass(frozen=True, slots=True)
class PermitEntityRecord:
    permit_id: str
    name: str | None
    address: str | None
    phone: str | None
    latitude: float
    longitude: float
    coordinate_unit: CoordinateUnit | None
    coordinate_unit_verified: bool


@dataclass(frozen=True, slots=True)
class CafeEntityRecord:
    cafe_id: str
    name: str | None
    address: str | None
    phone: str | None
    latitude: float
    longitude: float
    coordinate_unit: CoordinateUnit | None
    coordinate_unit_verified: bool


@dataclass(frozen=True, slots=True)
class EntityMatchEvidence:
    cafe_id: str
    normalized_address_exact: bool
    normalized_name_exact: bool
    normalized_phone_exact: bool
    within_distance_threshold: bool
    distance_m: float


@dataclass(frozen=True, slots=True)
class PermitCafeResolution:
    permit_id: str
    status: ResolutionStatus
    verified_cafe_id: str | None
    abstained: bool
    strong_candidate_count: int
    strong_candidates: tuple[EntityMatchEvidence, ...]


def normalize_entity_text(value: str | None) -> str | None:
    """Apply NFKC, case-folding, and whitespace collapse only.

    Punctuation and tokens stay intact.  This intentionally avoids fuzzy or
    lossy normalization that could turn distinct names/addresses into matches.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("entity text must be a string or None")
    normalized = unicodedata.normalize("NFKC", value).casefold()
    collapsed = " ".join(normalized.split())
    return collapsed or None


def normalize_phone_digits(
    value: str | None,
    *,
    min_digits: int = PERMIT_CAFE_ENTITY_MIN_PHONE_DIGITS,
) -> str | None:
    """Return ASCII phone digits while rejecting short or mixed-text values."""

    if not isinstance(min_digits, int) or isinstance(min_digits, bool) or min_digits < 1:
        raise ValueError("min_digits must be a positive integer")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("phone must be a string or None")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return None
    digits: list[str] = []
    for character in normalized:
        if "0" <= character <= "9":
            digits.append(character)
        elif character.isspace() or character in "+-().":
            continue
        else:
            raise ValueError("phone may contain only digits and common separators")
    result = "".join(digits)
    if len(result) < min_digits:
        raise ValueError(f"phone must contain at least {min_digits} digits")
    return result


def _validate_coordinates(
    *,
    latitude: float,
    longitude: float,
    unit: CoordinateUnit | None,
    unit_verified: bool,
    source: str,
) -> None:
    if unit_verified is not True:
        raise ValueError(f"{source} coordinate_unit_verified must be true")
    if unit != "wgs84_degrees":
        raise ValueError(f"{source} coordinate_unit must be wgs84_degrees")
    if (
        not isinstance(latitude, (int, float))
        or isinstance(latitude, bool)
        or not isinstance(longitude, (int, float))
        or isinstance(longitude, bool)
        or not isfinite(latitude)
        or not isfinite(longitude)
    ):
        raise ValueError(f"{source} coordinates must be finite numbers")
    # Shared primitive enforces WGS84 latitude/longitude ranges.
    haversine_m(latitude, longitude, latitude, longitude)


def _validate_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def resolve_permit_to_cafes(
    permit: PermitEntityRecord,
    cafes: Sequence[CafeEntityRecord],
    *,
    max_distance_m: float = PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
    min_phone_digits: int = PERMIT_CAFE_ENTITY_MIN_PHONE_DIGITS,
) -> PermitCafeResolution:
    """Resolve one permit against a caller-bounded cafe candidate sequence."""

    if (
        not isinstance(max_distance_m, (int, float))
        or isinstance(max_distance_m, bool)
        or not isfinite(max_distance_m)
        or max_distance_m <= 0
    ):
        raise ValueError("max_distance_m must be finite and positive")
    if (
        not isinstance(min_phone_digits, int)
        or isinstance(min_phone_digits, bool)
        or min_phone_digits < 1
    ):
        raise ValueError("min_phone_digits must be a positive integer")
    _validate_id(permit.permit_id, "permit_id")
    _validate_coordinates(
        latitude=permit.latitude,
        longitude=permit.longitude,
        unit=permit.coordinate_unit,
        unit_verified=permit.coordinate_unit_verified,
        source="permit",
    )
    permit_name = normalize_entity_text(permit.name)
    permit_address = normalize_entity_text(permit.address)
    permit_phone = normalize_phone_digits(permit.phone, min_digits=min_phone_digits)

    for cafe in cafes:
        _validate_id(cafe.cafe_id, "cafe_id")
    sorted_cafes = sorted(cafes, key=lambda item: item.cafe_id)
    if len({cafe.cafe_id for cafe in sorted_cafes}) != len(sorted_cafes):
        raise ValueError("duplicate cafe_id")

    strong_candidates: list[EntityMatchEvidence] = []
    for cafe in sorted_cafes:
        _validate_coordinates(
            latitude=cafe.latitude,
            longitude=cafe.longitude,
            unit=cafe.coordinate_unit,
            unit_verified=cafe.coordinate_unit_verified,
            source=f"cafe {cafe.cafe_id}",
        )
        cafe_name = normalize_entity_text(cafe.name)
        cafe_address = normalize_entity_text(cafe.address)
        cafe_phone = normalize_phone_digits(cafe.phone, min_digits=min_phone_digits)
        address_exact = (
            permit_address is not None and permit_address == cafe_address
        )
        name_exact = permit_name is not None and permit_name == cafe_name
        phone_exact = permit_phone is not None and permit_phone == cafe_phone
        distance_m = haversine_m(
            permit.latitude,
            permit.longitude,
            cafe.latitude,
            cafe.longitude,
        )
        within_distance = distance_m <= max_distance_m
        if not (address_exact and within_distance and (name_exact or phone_exact)):
            continue
        strong_candidates.append(
            EntityMatchEvidence(
                cafe_id=cafe.cafe_id,
                normalized_address_exact=address_exact,
                normalized_name_exact=name_exact,
                normalized_phone_exact=phone_exact,
                within_distance_threshold=within_distance,
                distance_m=distance_m,
            )
        )

    evidence = tuple(strong_candidates)
    if len(evidence) == 1:
        return PermitCafeResolution(
            permit_id=permit.permit_id,
            status="verified",
            verified_cafe_id=evidence[0].cafe_id,
            abstained=False,
            strong_candidate_count=1,
            strong_candidates=evidence,
        )
    status: ResolutionStatus = "missing" if not evidence else "ambiguous"
    return PermitCafeResolution(
        permit_id=permit.permit_id,
        status=status,
        verified_cafe_id=None,
        abstained=True,
        strong_candidate_count=len(evidence),
        strong_candidates=evidence,
    )
