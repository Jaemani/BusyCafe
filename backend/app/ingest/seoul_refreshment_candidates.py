"""Pure resolver for conservative cafe candidates from Seoul permits.

The output contract uses generic place fields so a later catalog reconciler
does not need to understand OA-16095 aliases.  This module never writes a DB
or file and never guesses across conflicting source rows.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.clients.seoul_refreshment_permits import (
    SeoulRefreshmentPermitAPIError,
    epsg5174_to_wgs84,
)
from app.config import SEOUL_BBOX, SEOUL_REFRESHMENT_PROVISIONAL_CAFE_TYPES
from app.schemas import SeoulRefreshmentPermit


@dataclass(frozen=True, slots=True)
class PlaceCandidate:
    """Source-neutral candidate record with explicit provenance."""

    source: str
    source_id: str
    name: str
    latitude: float
    longitude: float
    category: str
    road_address: str | None
    lot_address: str | None
    phone: str | None


@dataclass(frozen=True, slots=True)
class CandidateResolution:
    candidates: tuple[PlaceCandidate, ...]
    source_row_count: int
    unique_management_number_count: int
    exact_duplicate_row_count: int
    phone_variant_group_count: int
    phone_conflict_group_count: int
    quarantined_group_count: int
    quarantine_reason_counts: dict[str, int]
    exclusion_reason_counts: dict[str, int]
    candidate_category_counts: dict[str, int]


_CONFLICT_FIELDS: dict[str, tuple[str, ...]] = {
    "name_conflict": ("business_name",),
    "status_conflict": (
        "trade_status_code",
        "trade_status_name",
        "detail_status_code",
        "detail_status_name",
        "closure_date",
    ),
    "category_conflict": ("business_type", "hygiene_type"),
    "address_conflict": ("lot_address", "road_address"),
    "coordinate_conflict": ("projected_x_m", "projected_y_m"),
}


def normalize_phone(value: str | None) -> str | None:
    """Normalize punctuation only; country-code inference would be guessing."""

    if value is None:
        return None
    digits = "".join(character for character in value if character.isdecimal())
    return digits or None


def _row_values(row: SeoulRefreshmentPermit) -> dict[str, object]:
    return row.model_dump(mode="json", by_alias=False)


def _changed_fields(
    rows: Sequence[SeoulRefreshmentPermit],
) -> set[str]:
    first = _row_values(rows[0])
    changed: set[str] = set()
    for row in rows[1:]:
        current = _row_values(row)
        for field in first.keys() | current.keys():
            if first.get(field) != current.get(field):
                changed.add(field)
    return changed


def _conflict_reasons(changed_fields: set[str]) -> tuple[str, ...]:
    reasons = [
        reason
        for reason, fields in _CONFLICT_FIELDS.items()
        if changed_fields.intersection(fields)
    ]
    classified = {field for fields in _CONFLICT_FIELDS.values() for field in fields}
    if changed_fields - classified - {"phone"}:
        reasons.append("other_duplicate_conflict")
    return tuple(sorted(reasons))


def resolve_permit_candidates(
    rows: Iterable[SeoulRefreshmentPermit],
) -> CandidateResolution:
    """Resolve immutable candidates, excluding every ambiguous identity group."""

    grouped: dict[str, list[SeoulRefreshmentPermit]] = defaultdict(list)
    source_row_count = 0
    quarantine_reasons: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    for row in rows:
        source_row_count += 1
        if row.management_number is None:
            quarantine_reasons["missing_management_number"] += 1
            continue
        grouped[row.management_number].append(row)

    candidates: list[PlaceCandidate] = []
    exact_duplicate_rows = 0
    phone_variant_groups = 0
    phone_conflict_groups = 0
    quarantined_groups = sum(quarantine_reasons.values())
    categories = set(SEOUL_REFRESHMENT_PROVISIONAL_CAFE_TYPES)

    for management_number in sorted(grouped):
        group = grouped[management_number]
        fingerprints = [
            hashlib.sha256(
                repr(sorted(_row_values(row).items())).encode("utf-8")
            ).digest()
            for row in group
        ]
        exact_duplicate_rows += len(fingerprints) - len(set(fingerprints))
        changed_fields = _changed_fields(group)
        reasons = _conflict_reasons(changed_fields)
        if reasons:
            quarantined_groups += 1
            quarantine_reasons.update(reasons)
            continue

        phone: str | None
        normalized_phones = {normalize_phone(row.phone) for row in group}
        if changed_fields == {"phone"}:
            phone_variant_groups += 1
        if len(normalized_phones) == 1:
            phone = next(iter(normalized_phones))
        else:
            phone_conflict_groups += 1
            phone = None

        row = group[0]
        if row.business_type not in categories:
            exclusions["category_not_selected"] += 1
            continue
        if not row.is_reported_open:
            exclusions["not_reported_open"] += 1
            continue
        if not row.has_projected_coordinates:
            exclusions["missing_coordinates"] += 1
            continue
        try:
            point = epsg5174_to_wgs84(
                row.projected_x_m,  # type: ignore[arg-type]
                row.projected_y_m,  # type: ignore[arg-type]
            )
        except SeoulRefreshmentPermitAPIError:
            exclusions["invalid_coordinates"] += 1
            continue
        min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
        if not (
            min_lng <= point.longitude <= max_lng
            and min_lat <= point.latitude <= max_lat
        ):
            exclusions["outside_seoul_bbox"] += 1
            continue
        candidates.append(
            PlaceCandidate(
                source="seoul_refreshment_permits",
                source_id=management_number,
                name=row.business_name,
                latitude=point.latitude,
                longitude=point.longitude,
                category=row.business_type,
                road_address=row.road_address,
                lot_address=row.lot_address,
                phone=phone,
            )
        )

    candidate_counts = Counter(candidate.category for candidate in candidates)
    return CandidateResolution(
        candidates=tuple(sorted(candidates, key=lambda item: item.source_id)),
        source_row_count=source_row_count,
        unique_management_number_count=len(grouped),
        exact_duplicate_row_count=exact_duplicate_rows,
        phone_variant_group_count=phone_variant_groups,
        phone_conflict_group_count=phone_conflict_groups,
        quarantined_group_count=quarantined_groups,
        quarantine_reason_counts=dict(sorted(quarantine_reasons.items())),
        exclusion_reason_counts=dict(sorted(exclusions.items())),
        candidate_category_counts=dict(sorted(candidate_counts.items())),
    )


def select_review_sample(
    candidates: Sequence[PlaceCandidate], sample_size: int
) -> tuple[PlaceCandidate, ...]:
    """Select a stable, category-distributed sample using SHA-256 ranks."""

    if sample_size < 0:
        raise ValueError("sample_size must be >= 0")
    if sample_size == 0 or not candidates:
        return ()
    by_category: dict[str, list[PlaceCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_category[candidate.category].append(candidate)
    for category, values in by_category.items():
        values.sort(
            key=lambda candidate: hashlib.sha256(
                f"{category}\0{candidate.source_id}".encode("utf-8")
            ).digest()
        )

    selected: list[PlaceCandidate] = []
    categories = sorted(by_category)
    while len(selected) < min(sample_size, len(candidates)):
        added = False
        for category in categories:
            if by_category[category]:
                selected.append(by_category[category].pop(0))
                added = True
                if len(selected) == min(sample_size, len(candidates)):
                    break
        if not added:
            break
    return tuple(selected)
