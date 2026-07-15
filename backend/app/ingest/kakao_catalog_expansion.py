"""Pure, fail-closed selection of Kakao-owned cafe catalog additions."""

from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from app.config import (
    KAKAO_CAFE_CATEGORY_CODE,
    PERMIT_RECONCILE_EXACT_NAME_MAX_M,
    PERMIT_RECONCILE_EXACT_PHONE_MAX_M,
    SEOUL_BBOX,
)
from app.geo import haversine_m
from app.ingest.permit_reconciliation import normalize_name
from app.ingest.seoul_refreshment_candidates import normalize_phone
from app.schemas import KakaoPlace


KAKAO_CANONICAL_SOURCE = "kakao"
_SEOUL_ADDRESS_PREFIXES = frozenset({"서울", "서울시", "서울특별시"})


@dataclass(frozen=True, slots=True)
class CanonicalCafeIdentity:
    canonical_id: int
    name: str
    latitude: float
    longitude: float
    road_address: str | None
    phone: str | None


@dataclass(frozen=True, slots=True)
class KakaoCanonicalCandidate:
    canonical_source: str
    canonical_source_id: str
    name: str
    latitude: float
    longitude: float
    category: str
    road_address: str | None
    lot_address: str | None
    phone: str | None
    direct_url: str


@dataclass(frozen=True, slots=True)
class KakaoExpansionConflict:
    kakao_place_id: str
    rules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KakaoExpansionReport:
    kakao_input_count: int
    outside_target_region_count: int
    outside_target_bbox_count: int
    unique_kakao_place_count: int
    duplicate_kakao_place_id_count: int
    canonical_cafe_count: int
    existing_kakao_provider_id_count: int
    existing_provider_id_in_cache_count: int
    existing_provider_id_missing_from_cache_count: int
    unmatched_kakao_place_count: int
    canonical_collision_count: int
    peer_collision_count: int
    conflict_count: int
    blocking_conflict_count: int
    advisory_conflict_count: int
    conflict_rule_counts: dict[str, int]
    candidate_count: int
    candidate_ids_sha256: str
    human_apply_required: bool


@dataclass(frozen=True, slots=True)
class KakaoExpansionBuild:
    candidates: tuple[KakaoCanonicalCandidate, ...]
    conflicts: tuple[KakaoExpansionConflict, ...]
    report: KakaoExpansionReport


def _address_core(value: str | None) -> str | None:
    if not value:
        return None
    parts = unicodedata.normalize("NFKC", value).casefold().split()
    if parts and parts[0] in {"서울", "서울시", "서울특별시"}:
        parts = parts[1:]
    if parts and parts[0].endswith("구"):
        parts = parts[1:]
    core = "".join(
        character
        for character in "".join(parts)
        if character.isalnum()
    )
    if not any(character.isalpha() for character in core):
        return None
    if not any(character.isdecimal() for character in core):
        return None
    return core


def _place_addresses(place: KakaoPlace) -> frozenset[str]:
    return frozenset(
        normalized
        for value in (place.road_address_name, place.address_name)
        if (normalized := _address_core(value)) is not None
    )


def _has_seoul_address(place: KakaoPlace) -> bool:
    for value in (place.road_address_name, place.address_name):
        parts = unicodedata.normalize("NFKC", value).strip().split()
        if parts and parts[0] in _SEOUL_ADDRESS_PREFIXES:
            return True
    return False


def _is_within_seoul_bbox(place: KakaoPlace) -> bool:
    """Validate Kakao's documented ``x=longitude, y=latitude`` contract."""

    min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
    return (
        min_lng <= place.longitude <= max_lng
        and min_lat <= place.latitude <= max_lat
    )


def is_target_seoul_kakao_place(place: KakaoPlace) -> bool:
    """Return whether address and WGS84 coordinate gates both pass."""

    return _has_seoul_address(place) and _is_within_seoul_bbox(place)


def _candidate(place: KakaoPlace) -> KakaoCanonicalCandidate:
    return KakaoCanonicalCandidate(
        canonical_source=KAKAO_CANONICAL_SOURCE,
        canonical_source_id=place.place_id,
        name=place.place_name,
        latitude=place.latitude,
        longitude=place.longitude,
        category=place.category_name or place.category_group_code,
        road_address=place.road_address_name or None,
        lot_address=place.address_name or None,
        phone=place.phone or None,
        direct_url=f"https://place.map.kakao.com/{place.place_id}",
    )


def _add_nearby_pair_conflicts(
    groups: dict[str, list[KakaoPlace]],
    conflicts: dict[str, set[str]],
    *,
    maximum_distance_m: float,
    rule: str,
) -> None:
    for group in groups.values():
        for first, second in combinations(group, 2):
            if (
                haversine_m(
                    first.latitude,
                    first.longitude,
                    second.latitude,
                    second.longitude,
                )
                <= maximum_distance_m
            ):
                conflicts[first.place_id].add(rule)
                conflicts[second.place_id].add(rule)


def build_kakao_expansion(
    kakao_places: Sequence[KakaoPlace],
    canonical_cafes: Sequence[CanonicalCafeIdentity],
    existing_kakao_provider_ids: Sequence[str],
) -> KakaoExpansionBuild:
    """Select unused Kakao CE7 identities, quarantining strong collisions.

    Every selected row uses the Kakao place ID as its canonical origin.  The
    function performs no I/O and deliberately has no write/apply mode.
    """

    canonical_by_id: dict[int, CanonicalCafeIdentity] = {}
    for cafe in canonical_cafes:
        if cafe.canonical_id in canonical_by_id:
            raise ValueError(f"duplicate canonical cafe ID: {cafe.canonical_id}")
        canonical_by_id[cafe.canonical_id] = cafe

    provider_ids = tuple(existing_kakao_provider_ids)
    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("duplicate existing Kakao provider ID")
    for place_id in provider_ids:
        if not place_id.isascii() or not place_id.isdigit():
            raise ValueError("existing Kakao provider ID must be ASCII digits")
    used_ids = frozenset(provider_ids)

    place_groups: dict[str, list[KakaoPlace]] = defaultdict(list)
    all_input_ids: set[str] = set()
    outside_target_region_count = 0
    outside_target_bbox_count = 0
    for place in kakao_places:
        if place.category_group_code != KAKAO_CAFE_CATEGORY_CODE:
            raise ValueError(f"Kakao place is not CE7: {place.place_id}")
        if not place.place_id.isascii() or not place.place_id.isdigit():
            raise ValueError("Kakao place ID must be ASCII digits")
        all_input_ids.add(place.place_id)
        has_seoul_address = _has_seoul_address(place)
        within_seoul_bbox = _is_within_seoul_bbox(place)
        if not has_seoul_address:
            outside_target_region_count += 1
        if not within_seoul_bbox:
            outside_target_bbox_count += 1
        if not has_seoul_address or not within_seoul_bbox:
            continue
        place_groups[place.place_id].append(place)
    duplicate_ids = {
        place_id for place_id, records in place_groups.items() if len(records) > 1
    }
    unique_places = {
        place_id: records[0]
        for place_id, records in place_groups.items()
        if len(records) == 1
    }
    unlinked = tuple(
        unique_places[place_id]
        for place_id in sorted(unique_places)
        if place_id not in used_ids
    )

    canonical_names: dict[str, list[CanonicalCafeIdentity]] = defaultdict(list)
    canonical_phones: dict[str, list[CanonicalCafeIdentity]] = defaultdict(list)
    canonical_coordinates: dict[
        tuple[float, float], list[CanonicalCafeIdentity]
    ] = defaultdict(list)
    canonical_addresses: dict[int, frozenset[str]] = {}
    for cafe in canonical_by_id.values():
        name = normalize_name(cafe.name)
        if name:
            canonical_names[name].append(cafe)
        phone = normalize_phone(cafe.phone)
        if phone:
            canonical_phones[phone].append(cafe)
        canonical_coordinates[(cafe.latitude, cafe.longitude)].append(cafe)
        address = _address_core(cafe.road_address)
        canonical_addresses[cafe.canonical_id] = (
            frozenset((address,)) if address else frozenset()
        )

    conflicts: dict[str, set[str]] = defaultdict(set)
    canonical_collision_ids: set[str] = set()
    for place in unlinked:
        name = normalize_name(place.place_name)
        phone = normalize_phone(place.phone)
        addresses = _place_addresses(place)
        coordinate = (place.latitude, place.longitude)
        matched = False
        if canonical_coordinates.get(coordinate):
            conflicts[place.place_id].add("canonical_exact_coordinate")
            matched = True
        for cafe in canonical_names.get(name, ()) if name else ():
            if addresses & canonical_addresses[cafe.canonical_id]:
                conflicts[place.place_id].add(
                    "canonical_exact_name_and_address"
                )
                matched = True
            if (
                haversine_m(
                    place.latitude,
                    place.longitude,
                    cafe.latitude,
                    cafe.longitude,
                )
                <= PERMIT_RECONCILE_EXACT_NAME_MAX_M
            ):
                conflicts[place.place_id].add("canonical_exact_name_nearby")
                matched = True
        for cafe in canonical_phones.get(phone, ()) if phone else ():
            if (
                haversine_m(
                    place.latitude,
                    place.longitude,
                    cafe.latitude,
                    cafe.longitude,
                )
                <= PERMIT_RECONCILE_EXACT_PHONE_MAX_M
            ):
                conflicts[place.place_id].add("canonical_exact_phone_nearby")
                matched = True
        if matched:
            canonical_collision_ids.add(place.place_id)

    peer_names: dict[str, list[KakaoPlace]] = defaultdict(list)
    peer_phones: dict[str, list[KakaoPlace]] = defaultdict(list)
    peer_coordinates: dict[tuple[float, float], list[KakaoPlace]] = defaultdict(list)
    peer_name_addresses: dict[tuple[str, str], list[KakaoPlace]] = defaultdict(list)
    for place in unlinked:
        name = normalize_name(place.place_name)
        phone = normalize_phone(place.phone)
        if name:
            peer_names[name].append(place)
            for address in _place_addresses(place):
                peer_name_addresses[(name, address)].append(place)
        if phone:
            peer_phones[phone].append(place)
        peer_coordinates[(place.latitude, place.longitude)].append(place)

    peer_collision_ids: set[str] = set()
    for group in peer_coordinates.values():
        if len(group) > 1:
            for place in group:
                conflicts[place.place_id].add("peer_exact_coordinate")
                peer_collision_ids.add(place.place_id)
    for group in peer_name_addresses.values():
        if len(group) > 1:
            for place in group:
                conflicts[place.place_id].add("peer_exact_name_and_address")
                peer_collision_ids.add(place.place_id)
    before_nearby = {place_id: set(rules) for place_id, rules in conflicts.items()}
    _add_nearby_pair_conflicts(
        peer_names,
        conflicts,
        maximum_distance_m=PERMIT_RECONCILE_EXACT_NAME_MAX_M,
        rule="peer_exact_name_nearby",
    )
    _add_nearby_pair_conflicts(
        peer_phones,
        conflicts,
        maximum_distance_m=PERMIT_RECONCILE_EXACT_PHONE_MAX_M,
        rule="peer_exact_phone_nearby",
    )
    for place_id, rules in conflicts.items():
        previous = before_nearby.get(place_id, set())
        if rules - previous:
            peer_collision_ids.add(place_id)

    conflict_records = tuple(
        KakaoExpansionConflict(place_id, tuple(sorted(conflicts[place_id])))
        for place_id in sorted(conflicts)
    )
    candidates = tuple(
        _candidate(place)
        for place in unlinked
        # A Kakao place ID is an authoritative provider identity. Multiple
        # distinct IDs at one coordinate or sharing a chain phone number are
        # common in malls and dense streets, so peer signals are audit-only.
        # Only a strong collision with an existing canonical cafe blocks a new
        # canonical row.
        if place.place_id not in canonical_collision_ids
    )
    candidate_ids = "\n".join(
        candidate.canonical_source_id for candidate in candidates
    ).encode("ascii")
    rule_counts = Counter(
        rule for conflict in conflict_records for rule in conflict.rules
    )
    provider_ids_in_cache = used_ids & all_input_ids
    return KakaoExpansionBuild(
        candidates=candidates,
        conflicts=conflict_records,
        report=KakaoExpansionReport(
            kakao_input_count=len(kakao_places),
            outside_target_region_count=outside_target_region_count,
            outside_target_bbox_count=outside_target_bbox_count,
            unique_kakao_place_count=len(unique_places),
            duplicate_kakao_place_id_count=len(duplicate_ids),
            canonical_cafe_count=len(canonical_by_id),
            existing_kakao_provider_id_count=len(used_ids),
            existing_provider_id_in_cache_count=len(provider_ids_in_cache),
            existing_provider_id_missing_from_cache_count=(
                len(used_ids) - len(provider_ids_in_cache)
            ),
            unmatched_kakao_place_count=len(unlinked),
            canonical_collision_count=len(canonical_collision_ids),
            peer_collision_count=len(peer_collision_ids),
            conflict_count=len(conflict_records),
            blocking_conflict_count=len(canonical_collision_ids),
            advisory_conflict_count=len(
                peer_collision_ids - canonical_collision_ids
            ),
            conflict_rule_counts=dict(sorted(rule_counts.items())),
            candidate_count=len(candidates),
            candidate_ids_sha256=hashlib.sha256(candidate_ids).hexdigest(),
            human_apply_required=True,
        ),
    )
