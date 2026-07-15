"""Pure conservative entity resolution between permits and cafe places.

The sources share no identifier.  A cafe is therefore a strong candidate only
when Seoul road-address components are exactly equal, WGS84 distance is within
the fixed gate, and either normalized name or normalized phone is exactly
equal. One strong candidate verifies; zero is missing; multiple candidates are
ambiguous and explicitly abstain. No fuzzy matching, I/O, database write, or
public catalog mutation occurs here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
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
    structured_address_exact: bool
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
    reverse_identity_collision: bool
    strong_candidate_count: int
    strong_candidates: tuple[EntityMatchEvidence, ...]


@dataclass(frozen=True, slots=True)
class PermitCafeCandidateSet:
    """One permit and its caller-bounded cafe candidates."""

    permit: PermitEntityRecord
    cafes: tuple[CafeEntityRecord, ...]


@dataclass(frozen=True, slots=True)
class PermitCafeBatchResolution:
    """Globally one-to-one, deterministic permit/cafe resolutions."""

    resolutions: tuple[PermitCafeResolution, ...]
    reverse_collision_permit_count: int
    reverse_collision_cafe_count: int


@dataclass(frozen=True, slots=True)
class SeoulRoadAddressComponents:
    """Canonical administrative components of one Seoul road address."""

    city: Literal["서울특별시"]
    district: str
    road_name: str
    building_main: int
    building_sub: int | None


SEOUL_DISTRICTS = frozenset(
    {
        "강남구",
        "강동구",
        "강북구",
        "강서구",
        "관악구",
        "광진구",
        "구로구",
        "금천구",
        "노원구",
        "도봉구",
        "동대문구",
        "동작구",
        "마포구",
        "서대문구",
        "서초구",
        "성동구",
        "성북구",
        "송파구",
        "양천구",
        "영등포구",
        "용산구",
        "은평구",
        "종로구",
        "중구",
        "중랑구",
    }
)
_SEOUL_ROAD_ADDRESS_RE = re.compile(
    r"^(?:서울특별시|서울시|서울)\s+"
    r"(?P<district>[가-힣]+구)\s+"
    r"(?P<road>[0-9가-힣·]+(?:로|길))\s*"
    r"(?P<main>[1-9][0-9]*)"
    r"(?:-(?P<sub>[1-9][0-9]*))?"
    r"(?P<suffix>.*)$"
)
_ADDRESS_UNIT_RE = re.compile(
    r"^(?:(?:지하|지상)?제?\s*[0-9]+\s*층|[bB]\s*[0-9]+\s*층|옥탑층|"
    r"제?\s*[0-9]+\s*호|(?:제?\s*[0-9]+|[A-Za-z])\s*동)"
)


def _leading_parenthetical_end(value: str) -> int | None:
    """Return balanced leading parenthetical end, including nested groups."""

    if not value.startswith("("):
        return None
    depth = 0
    has_content = False
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index + 1 if has_content else None
        elif depth > 0 and not character.isspace():
            has_content = True
    return None


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


def _has_only_ignorable_address_suffix(value: str) -> bool:
    """Accept parenthetical/floor/unit detail only after building extraction."""

    remaining = value.strip()
    while remaining:
        if remaining.startswith(","):
            remaining = remaining[1:].lstrip()
            if not remaining:
                return False
        parenthetical_end = _leading_parenthetical_end(remaining)
        if parenthetical_end is not None:
            remaining = remaining[parenthetical_end:].strip()
            continue
        unit_match = _ADDRESS_UNIT_RE.match(remaining)
        if unit_match is None:
            return False
        remaining = remaining[unit_match.end() :].strip()
    return True


def parse_seoul_road_address(
    value: str | None,
) -> SeoulRoadAddressComponents | None:
    """Parse exact Seoul road-address components, otherwise abstain.

    City aliases are administrative aliases, not fuzzy matches. Districts are
    restricted to Seoul's 25 gu. Road name and building main/sub number remain
    exact after NFKC. Parenthetical and floor/unit detail may be discarded only
    after the building number was parsed successfully.
    """

    normalized = normalize_entity_text(value)
    if normalized is None:
        return None
    match = _SEOUL_ROAD_ADDRESS_RE.fullmatch(normalized)
    if match is None or match.group("district") not in SEOUL_DISTRICTS:
        return None
    if not _has_only_ignorable_address_suffix(match.group("suffix")):
        return None
    return SeoulRoadAddressComponents(
        city="서울특별시",
        district=match.group("district"),
        road_name=match.group("road"),
        building_main=int(match.group("main")),
        building_sub=(
            int(match.group("sub")) if match.group("sub") is not None else None
        ),
    )


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
    permit_address = parse_seoul_road_address(permit.address)
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
        cafe_address = parse_seoul_road_address(cafe.address)
        cafe_phone = normalize_phone_digits(cafe.phone, min_digits=min_phone_digits)
        address_exact = permit_address is not None and permit_address == cafe_address
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
                structured_address_exact=address_exact,
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
            reverse_identity_collision=False,
            strong_candidate_count=1,
            strong_candidates=evidence,
        )
    status: ResolutionStatus = "missing" if not evidence else "ambiguous"
    return PermitCafeResolution(
        permit_id=permit.permit_id,
        status=status,
        verified_cafe_id=None,
        abstained=True,
        reverse_identity_collision=False,
        strong_candidate_count=len(evidence),
        strong_candidates=evidence,
    )


def resolve_permit_candidate_sets(
    candidate_sets: Sequence[PermitCafeCandidateSet],
    *,
    max_distance_m: float = PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
    min_phone_digits: int = PERMIT_CAFE_ENTITY_MIN_PHONE_DIGITS,
) -> PermitCafeBatchResolution:
    """Resolve a batch and enforce global one-permit-to-one-cafe identity.

    Forward ambiguity is retained by the single-permit resolver. If two or more
    otherwise verified permits point at one cafe ID, every involved permit is
    converted to an explicit ambiguous abstention. No winner is guessed.
    """

    for candidate_set in candidate_sets:
        _validate_id(candidate_set.permit.permit_id, "permit_id")
    ordered = sorted(candidate_sets, key=lambda item: item.permit.permit_id)
    if len({item.permit.permit_id for item in ordered}) != len(ordered):
        raise ValueError("duplicate permit_id")
    provisional = tuple(
        resolve_permit_to_cafes(
            item.permit,
            item.cafes,
            max_distance_m=max_distance_m,
            min_phone_digits=min_phone_digits,
        )
        for item in ordered
    )
    verified_permits_by_cafe: dict[str, list[str]] = {}
    for resolution in provisional:
        if resolution.status != "verified":
            continue
        if resolution.verified_cafe_id is None:
            raise ValueError("verified resolution must contain cafe ID")
        verified_permits_by_cafe.setdefault(
            resolution.verified_cafe_id, []
        ).append(resolution.permit_id)
    collided_cafes = {
        cafe_id
        for cafe_id, permit_ids in verified_permits_by_cafe.items()
        if len(permit_ids) > 1
    }
    collided_permits = {
        permit_id
        for cafe_id in collided_cafes
        for permit_id in verified_permits_by_cafe[cafe_id]
    }
    resolutions = tuple(
        replace(
            resolution,
            status="ambiguous",
            verified_cafe_id=None,
            abstained=True,
            reverse_identity_collision=True,
        )
        if resolution.permit_id in collided_permits
        else resolution
        for resolution in provisional
    )
    return PermitCafeBatchResolution(
        resolutions=resolutions,
        reverse_collision_permit_count=len(collided_permits),
        reverse_collision_cafe_count=len(collided_cafes),
    )
