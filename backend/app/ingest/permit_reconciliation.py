"""Pure, conservative reconciliation of permit candidates to a POI catalog."""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from app.geo import haversine_m
from app.config import (
    PERMIT_RECONCILE_EXACT_NAME_MAX_M,
    PERMIT_RECONCILE_EXACT_PHONE_MAX_M,
)
from app.ingest.seoul_refreshment_candidates import PlaceCandidate, normalize_phone


GRID_CELL_M = PERMIT_RECONCILE_EXACT_PHONE_MAX_M
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class CatalogPlace:
    catalog_id: str
    name: str
    latitude: float
    longitude: float
    category: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationMatch:
    candidate: PlaceCandidate
    catalog: CatalogPlace
    distance_m: float
    rule: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    candidate_count: int
    catalog_count: int
    matches: tuple[ReconciliationMatch, ...]
    ambiguous: tuple[PlaceCandidate, ...]
    unmatched: tuple[PlaceCandidate, ...]
    candidate_category_counts: dict[str, int]
    matched_category_counts: dict[str, int]
    ambiguous_category_counts: dict[str, int]
    unmatched_category_counts: dict[str, int]
    match_rule_counts: dict[str, int]
    distance_check_count: int


@dataclass(frozen=True, slots=True)
class _PotentialMatch:
    catalog: CatalogPlace
    distance_m: float
    rule: str


def normalize_name(value: str) -> str:
    """Normalize representation only; no token deletion or fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _xy(latitude: float, longitude: float, reference_latitude: float) -> tuple[float, float]:
    return (
        EARTH_RADIUS_M
        * math.radians(longitude)
        * math.cos(math.radians(reference_latitude)),
        EARTH_RADIUS_M * math.radians(latitude),
    )


def _cell(x: float, y: float) -> tuple[int, int]:
    return math.floor(x / GRID_CELL_M), math.floor(y / GRID_CELL_M)


def _counts(values: Sequence[PlaceCandidate]) -> dict[str, int]:
    return dict(sorted(Counter(value.category for value in values).items()))


def reconcile_candidates(
    candidates: Sequence[PlaceCandidate],
    catalog: Sequence[CatalogPlace],
) -> ReconciliationResult:
    """Return only one-to-one strong matches; every collision stays ambiguous."""

    all_latitudes = [candidate.latitude for candidate in candidates]
    all_latitudes.extend(place.latitude for place in catalog)
    reference_latitude = (
        sum(all_latitudes) / len(all_latitudes) if all_latitudes else 37.5665
    )
    grid: dict[tuple[int, int], list[CatalogPlace]] = defaultdict(list)
    for place in catalog:
        x, y = _xy(place.latitude, place.longitude, reference_latitude)
        grid[_cell(x, y)].append(place)
    for places in grid.values():
        places.sort(key=lambda place: place.catalog_id)

    potentials: dict[str, tuple[_PotentialMatch, ...]] = {}
    candidate_by_id: dict[str, PlaceCandidate] = {}
    distance_checks = 0
    for candidate in sorted(candidates, key=lambda value: value.source_id):
        if candidate.source_id in candidate_by_id:
            raise ValueError(f"duplicate candidate source_id: {candidate.source_id}")
        candidate_by_id[candidate.source_id] = candidate
        candidate_name = normalize_name(candidate.name)
        candidate_phone = normalize_phone(candidate.phone)
        x, y = _xy(candidate.latitude, candidate.longitude, reference_latitude)
        cell_x, cell_y = _cell(x, y)
        matches: list[_PotentialMatch] = []
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for place in grid.get((cell_x + offset_x, cell_y + offset_y), ()):
                    name_equal = bool(candidate_name) and candidate_name == normalize_name(
                        place.name
                    )
                    place_phone = normalize_phone(place.phone)
                    phone_equal = bool(candidate_phone) and candidate_phone == place_phone
                    if not name_equal and not phone_equal:
                        continue
                    distance_checks += 1
                    distance = haversine_m(
                        candidate.latitude,
                        candidate.longitude,
                        place.latitude,
                        place.longitude,
                    )
                    name_match = (
                        name_equal
                        and distance <= PERMIT_RECONCILE_EXACT_NAME_MAX_M
                    )
                    phone_match = (
                        phone_equal
                        and distance <= PERMIT_RECONCILE_EXACT_PHONE_MAX_M
                    )
                    if not name_match and not phone_match:
                        continue
                    if name_match and phone_match:
                        rule = "exact_name_and_phone"
                    elif name_match:
                        rule = "exact_name"
                    else:
                        rule = "exact_phone"
                    matches.append(
                        _PotentialMatch(catalog=place, distance_m=distance, rule=rule)
                    )
        potentials[candidate.source_id] = tuple(
            sorted(matches, key=lambda match: match.catalog.catalog_id)
        )

    candidates_by_catalog: dict[str, set[str]] = defaultdict(set)
    for candidate_id, matches in potentials.items():
        for match in matches:
            candidates_by_catalog[match.catalog.catalog_id].add(candidate_id)

    resolved: list[ReconciliationMatch] = []
    ambiguous: list[PlaceCandidate] = []
    unmatched: list[PlaceCandidate] = []
    for candidate_id in sorted(candidate_by_id):
        candidate = candidate_by_id[candidate_id]
        matches = potentials[candidate_id]
        if not matches:
            unmatched.append(candidate)
            continue
        if len(matches) != 1 or len(candidates_by_catalog[matches[0].catalog.catalog_id]) != 1:
            ambiguous.append(candidate)
            continue
        match = matches[0]
        resolved.append(
            ReconciliationMatch(
                candidate=candidate,
                catalog=match.catalog,
                distance_m=match.distance_m,
                rule=match.rule,
            )
        )

    return ReconciliationResult(
        candidate_count=len(candidates),
        catalog_count=len(catalog),
        matches=tuple(resolved),
        ambiguous=tuple(ambiguous),
        unmatched=tuple(unmatched),
        candidate_category_counts=_counts(candidates),
        matched_category_counts=_counts([match.candidate for match in resolved]),
        ambiguous_category_counts=_counts(ambiguous),
        unmatched_category_counts=_counts(unmatched),
        match_rule_counts=dict(sorted(Counter(match.rule for match in resolved).items())),
        distance_check_count=distance_checks,
    )


def select_unmatched_review_sample(
    unmatched: Sequence[PlaceCandidate], sample_size: int
) -> tuple[PlaceCandidate, ...]:
    """Return a hash-ranked round-robin sample across source categories."""

    if sample_size < 0:
        raise ValueError("sample_size must be >= 0")
    by_category: dict[str, list[PlaceCandidate]] = defaultdict(list)
    for candidate in unmatched:
        by_category[candidate.category].append(candidate)
    for category, values in by_category.items():
        values.sort(
            key=lambda candidate: hashlib.sha256(
                f"{category}\0{candidate.source_id}".encode("utf-8")
            ).digest()
        )
    selected: list[PlaceCandidate] = []
    target = min(sample_size, len(unmatched))
    categories = sorted(by_category)
    while len(selected) < target:
        for category in categories:
            if by_category[category]:
                selected.append(by_category[category].pop(0))
                if len(selected) == target:
                    break
    return tuple(selected)
