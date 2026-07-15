#!/usr/bin/env python3
"""Profile all OA-16095 rows without writing to the cafe catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from math import ceil
from pathlib import Path
from tempfile import NamedTemporaryFile

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.seoul_refreshment_permits import (  # noqa: E402
    SeoulRefreshmentPermitAPIError,
    SeoulRefreshmentPermitClient,
    epsg5174_to_wgs84,
)
from app.config import (  # noqa: E402
    SEOUL_BBOX,
    SEOUL_REFRESHMENT_PERMIT_AREA_EXTREME_ABS_THRESHOLD_RAW,
    SEOUL_REFRESHMENT_PERMIT_AREA_PERCENTILES,
    SEOUL_REFRESHMENT_PERMIT_AREA_PROFILE_VERSION,
    SEOUL_REFRESHMENT_PERMIT_AREA_TARGET_BUSINESS_TYPE,
    SEOUL_REFRESHMENT_PERMIT_AREA_UNIT,
    SEOUL_REFRESHMENT_PERMIT_AREA_UNIT_PROVENANCE,
    SEOUL_REFRESHMENT_PERMIT_AREA_UNIT_STATUS,
    SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE,
    SEOUL_REFRESHMENT_PERMIT_PROFILE_PATH,
    SEOUL_REFRESHMENT_PERMIT_SERVICE,
    SEOUL_REFRESHMENT_PROVISIONAL_CAFE_TYPES,
    get_settings,
)
from app.schemas import SeoulRefreshmentPermit, SeoulRefreshmentPermitPage  # noqa: E402


class PermitProfileError(RuntimeError):
    """Raised when a moving or malformed source cannot produce a safe profile."""


@dataclass(frozen=True, slots=True)
class DecimalFieldProfile:
    source_field: str
    unit: str
    unit_status: str
    unit_provenance: str
    target_row_count: int
    blank_count: int
    nonblank_count: int
    numeric_count: int
    nonnumeric_count: int
    zero_count: int
    negative_count: int
    positive_count: int
    extreme_abs_threshold: str
    extreme_count: int
    minimum: str | None
    p1: str | None
    p5: str | None
    p50: str | None
    p95: str | None
    p99: str | None
    maximum: str | None


@dataclass(frozen=True, slots=True)
class VenueAreaProfile:
    profile_version: str
    target_business_type: str
    target_state: str
    unique_target_row_count: int
    facility_total_scope: DecimalFieldProfile
    site_area: DecimalFieldProfile
    both_nonblank_count: int
    both_numeric_count: int
    exact_decimal_equal_count: int
    exact_decimal_different_count: int
    road_address_nonblank_count: int
    lot_address_nonblank_count: int
    any_address_nonblank_count: int
    both_addresses_nonblank_count: int
    missing_address_count: int
    coordinate_pair_nonblank_count: int
    coordinate_partial_count: int
    coordinate_missing_count: int
    valid_seoul_coordinate_count: int
    invalid_coordinate_count: int
    outside_seoul_bbox_count: int
    row_retention: str


@dataclass(frozen=True, slots=True)
class PermitProfile:
    service: str
    total_count: int
    page_count: int
    row_count: int
    unique_management_number_count: int
    duplicate_row_count: int
    identical_duplicate_row_count: int
    conflicting_duplicate_row_count: int
    adjacent_duplicate_row_count: int
    conflicting_duplicate_later_newer_count: int
    conflicting_duplicate_earlier_newer_count: int
    conflicting_duplicate_timestamp_tie_count: int
    catalog_gate_passed: bool
    reported_open_count: int
    not_reported_open_count: int
    missing_coordinate_count: int
    invalid_coordinate_count: int
    outside_seoul_bbox_count: int
    valid_seoul_coordinate_count: int
    provisional_candidate_types: list[str]
    provisional_open_candidate_count: int
    provisional_open_candidate_with_valid_coordinate_count: int
    status_counts: dict[str, int]
    category_counts: dict[str, int]
    open_category_counts: dict[str, int]
    provisional_open_candidate_counts: dict[str, int]
    conflicting_duplicate_field_counts: dict[str, int]
    open_coffee_shop_venue_area: VenueAreaProfile


@dataclass(slots=True)
class _DecimalFieldAccumulator:
    source_field: str
    raw_values: list[str | None]
    decimal_values: list[Decimal | None]

    def add(self, raw: str | None, value: Decimal | None) -> None:
        self.raw_values.append(raw)
        self.decimal_values.append(value)


@dataclass(frozen=True, slots=True)
class _VenueAreaObservation:
    is_target: bool
    facility_raw: str | None
    facility_decimal: Decimal | None
    site_raw: str | None
    site_decimal: Decimal | None
    has_road_address: bool
    has_lot_address: bool
    coordinate_status: str


def _venue_area_observation(row: SeoulRefreshmentPermit) -> _VenueAreaObservation:
    if row.projected_x_m is None and row.projected_y_m is None:
        coordinate_status = "missing"
    elif row.projected_x_m is None or row.projected_y_m is None:
        coordinate_status = "partial"
    else:
        try:
            point = epsg5174_to_wgs84(row.projected_x_m, row.projected_y_m)
        except SeoulRefreshmentPermitAPIError:
            coordinate_status = "invalid"
        else:
            min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
            coordinate_status = (
                "valid_seoul"
                if min_lng <= point.longitude <= max_lng
                and min_lat <= point.latitude <= max_lat
                else "outside_seoul"
            )
    return _VenueAreaObservation(
        is_target=(
            row.is_reported_open
            and row.business_type == SEOUL_REFRESHMENT_PERMIT_AREA_TARGET_BUSINESS_TYPE
        ),
        facility_raw=row.facility_total_scope_raw,
        facility_decimal=row.facility_total_scope_decimal,
        site_raw=row.site_area_raw,
        site_decimal=row.site_area_decimal,
        has_road_address=row.road_address is not None,
        has_lot_address=row.lot_address is not None,
        coordinate_status=coordinate_status,
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _nearest_rank(values: list[Decimal], percent: int) -> Decimal | None:
    if not values:
        return None
    if percent < 1 or percent > 100:
        raise ValueError("percent must be in 1..100")
    rank = (percent * len(values) + 99) // 100
    return values[rank - 1]


def _finalize_decimal_field(
    accumulator: _DecimalFieldAccumulator,
) -> DecimalFieldProfile:
    values = sorted(value for value in accumulator.decimal_values if value is not None)
    percentiles = {
        label: _decimal_text(_nearest_rank(values, percent))
        for label, percent in SEOUL_REFRESHMENT_PERMIT_AREA_PERCENTILES
    }
    return DecimalFieldProfile(
        source_field=accumulator.source_field,
        unit=SEOUL_REFRESHMENT_PERMIT_AREA_UNIT,
        unit_status=SEOUL_REFRESHMENT_PERMIT_AREA_UNIT_STATUS,
        unit_provenance=SEOUL_REFRESHMENT_PERMIT_AREA_UNIT_PROVENANCE,
        target_row_count=len(accumulator.raw_values),
        blank_count=sum(value is None for value in accumulator.raw_values),
        nonblank_count=sum(value is not None for value in accumulator.raw_values),
        numeric_count=len(values),
        nonnumeric_count=sum(
            raw is not None and value is None
            for raw, value in zip(
                accumulator.raw_values, accumulator.decimal_values, strict=True
            )
        ),
        zero_count=sum(value == 0 for value in values),
        negative_count=sum(value < 0 for value in values),
        positive_count=sum(value > 0 for value in values),
        extreme_abs_threshold=_decimal_text(
            SEOUL_REFRESHMENT_PERMIT_AREA_EXTREME_ABS_THRESHOLD_RAW
        )
        or "0",
        extreme_count=sum(
            abs(value) >= SEOUL_REFRESHMENT_PERMIT_AREA_EXTREME_ABS_THRESHOLD_RAW
            for value in values
        ),
        minimum=_decimal_text(values[0] if values else None),
        p1=percentiles["p1"],
        p5=percentiles["p5"],
        p50=percentiles["p50"],
        p95=percentiles["p95"],
        p99=percentiles["p99"],
        maximum=_decimal_text(values[-1] if values else None),
    )


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def build_permit_profile(
    fetch_page: Callable[[int, int], SeoulRefreshmentPermitPage],
    *,
    page_size: int = SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE,
    candidate_types: tuple[str, ...] = SEOUL_REFRESHMENT_PROVISIONAL_CAFE_TYPES,
) -> PermitProfile:
    """Fetch a stable sequential view and aggregate only non-identifying facts."""

    if page_size < 1 or page_size > SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE:
        raise ValueError("page_size is outside the verified API bounds")
    if not candidate_types or any(not value.strip() for value in candidate_types):
        raise ValueError("candidate_types must contain non-empty exact categories")
    if len(set(candidate_types)) != len(candidate_types):
        raise ValueError("candidate_types must be unique")

    first = fetch_page(1, page_size)
    total_count = first.total_count
    page_count = max(1, ceil(total_count / page_size))
    seen_ids: set[str | None] = set()
    fingerprints_by_id: dict[str | None, str] = {}
    last_position_by_id: dict[str | None, int] = {}
    conflict_fields = (
        "trade_status_code",
        "trade_status_name",
        "detail_status_code",
        "detail_status_name",
        "closure_date",
        "phone",
        "lot_address",
        "road_address",
        "business_name",
        "last_modified_at",
        "source_updated_at",
        "business_type",
        "projected_x_m",
        "projected_y_m",
        "facility_total_scope_raw",
        "site_area_raw",
    )
    values_by_id: dict[str | None, tuple[object, ...]] = {}
    status_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    open_category_counts: Counter[str] = Counter()
    candidate_counts: Counter[str] = Counter()
    conflicting_field_counts: Counter[str] = Counter()
    open_count = missing_coordinates = invalid_coordinates = outside_seoul = 0
    valid_seoul = candidate_valid_seoul = row_count = duplicate_rows = 0
    identical_duplicates = conflicting_duplicates = adjacent_duplicates = 0
    conflict_later_newer = conflict_earlier_newer = conflict_timestamp_tie = 0
    candidates = set(candidate_types)
    area_observation_by_id: dict[
        str | None, tuple[str, str, _VenueAreaObservation]
    ] = {}

    def process_page(
        page: SeoulRefreshmentPermitPage,
        *,
        page_number: int,
        expected_rows: int,
    ) -> None:
        nonlocal open_count, missing_coordinates, invalid_coordinates
        nonlocal outside_seoul, valid_seoul, candidate_valid_seoul, row_count
        nonlocal duplicate_rows, identical_duplicates, conflicting_duplicates
        nonlocal adjacent_duplicates
        nonlocal conflict_later_newer, conflict_earlier_newer, conflict_timestamp_tie
        if page.total_count != total_count:
            raise PermitProfileError(
                f"source total changed during profile: {total_count} -> {page.total_count}"
            )
        if len(page.rows) != expected_rows:
            raise PermitProfileError(
                f"page {page_number} returned {len(page.rows)} rows, expected {expected_rows}"
            )
        for row in page.rows:
            row_count += 1
            fingerprint = hashlib.sha256(
                json.dumps(
                    row.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            conflict_values = tuple(getattr(row, field) for field in conflict_fields)
            area_selection = (
                row.source_updated_at or "",
                fingerprint,
                _venue_area_observation(row),
            )
            previous_area_selection = area_observation_by_id.get(row.management_number)
            if (
                previous_area_selection is None
                or area_selection[:2] > previous_area_selection[:2]
            ):
                area_observation_by_id[row.management_number] = area_selection
            if row.management_number in seen_ids:
                duplicate_rows += 1
                if fingerprints_by_id[row.management_number] == fingerprint:
                    identical_duplicates += 1
                else:
                    conflicting_duplicates += 1
                    previous_values = values_by_id[row.management_number]
                    changed = False
                    for field, previous, current in zip(
                        conflict_fields, previous_values, conflict_values, strict=True
                    ):
                        if previous != current:
                            conflicting_field_counts[field] += 1
                            changed = True
                    if not changed:
                        conflicting_field_counts["unmodeled_source_fields"] += 1
                    updated_index = conflict_fields.index("source_updated_at")
                    previous_updated = previous_values[updated_index]
                    current_updated = conflict_values[updated_index]
                    if (
                        isinstance(previous_updated, str)
                        and isinstance(current_updated, str)
                        and current_updated > previous_updated
                    ):
                        conflict_later_newer += 1
                        fingerprints_by_id[row.management_number] = fingerprint
                        values_by_id[row.management_number] = conflict_values
                    elif (
                        isinstance(previous_updated, str)
                        and isinstance(current_updated, str)
                        and current_updated < previous_updated
                    ):
                        conflict_earlier_newer += 1
                    else:
                        conflict_timestamp_tie += 1
                if row_count - last_position_by_id[row.management_number] == 1:
                    adjacent_duplicates += 1
                last_position_by_id[row.management_number] = row_count
                continue
            seen_ids.add(row.management_number)
            fingerprints_by_id[row.management_number] = fingerprint
            last_position_by_id[row.management_number] = row_count
            values_by_id[row.management_number] = conflict_values
            category_counts[row.business_type] += 1
            status_counts[
                "|".join(
                    (
                        row.trade_status_code,
                        row.trade_status_name,
                        row.detail_status_code,
                        row.detail_status_name,
                    )
                )
            ] += 1
            is_open = row.is_reported_open
            is_candidate = is_open and row.business_type in candidates
            if is_open:
                open_count += 1
                open_category_counts[row.business_type] += 1
            if is_candidate:
                candidate_counts[row.business_type] += 1

            coordinate_is_valid_seoul = False
            if not row.has_projected_coordinates:
                missing_coordinates += 1
            else:
                try:
                    point = epsg5174_to_wgs84(
                        row.projected_x_m,  # type: ignore[arg-type]
                        row.projected_y_m,  # type: ignore[arg-type]
                    )
                except SeoulRefreshmentPermitAPIError:
                    invalid_coordinates += 1
                else:
                    min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
                    if (
                        min_lng <= point.longitude <= max_lng
                        and min_lat <= point.latitude <= max_lat
                    ):
                        valid_seoul += 1
                        coordinate_is_valid_seoul = True
                    else:
                        outside_seoul += 1
            if is_candidate and coordinate_is_valid_seoul:
                candidate_valid_seoul += 1

    for page_number in range(1, page_count + 1):
        start = (page_number - 1) * page_size + 1
        end = min(page_number * page_size, total_count)
        expected_rows = max(0, end - start + 1)
        page = first if page_number == 1 else fetch_page(start, end)
        process_page(page, page_number=page_number, expected_rows=expected_rows)

    if row_count != total_count:
        raise PermitProfileError(
            f"profile completeness mismatch: rows={row_count}, total={total_count}"
        )
    candidate_total = sum(candidate_counts.values())
    facility_scope = _DecimalFieldAccumulator("FACILTOTSCP", [], [])
    site_area = _DecimalFieldAccumulator("SITEAREA", [], [])
    venue_target_count = both_nonblank = both_numeric = 0
    exact_equal = exact_different = 0
    road_address = lot_address = any_address = both_addresses = missing_address = 0
    coordinate_pair = coordinate_partial = coordinate_missing = 0
    target_valid_seoul = target_invalid_coordinate = target_outside_seoul = 0
    for _updated_at, _fingerprint, observation in area_observation_by_id.values():
        if not observation.is_target:
            continue
        venue_target_count += 1
        facility_scope.add(observation.facility_raw, observation.facility_decimal)
        site_area.add(observation.site_raw, observation.site_decimal)
        both_nonblank += (
            observation.facility_raw is not None and observation.site_raw is not None
        )
        if (
            observation.facility_decimal is not None
            and observation.site_decimal is not None
        ):
            both_numeric += 1
            if observation.facility_decimal == observation.site_decimal:
                exact_equal += 1
            else:
                exact_different += 1
        road_address += observation.has_road_address
        lot_address += observation.has_lot_address
        any_address += observation.has_road_address or observation.has_lot_address
        both_addresses += observation.has_road_address and observation.has_lot_address
        missing_address += (
            not observation.has_road_address and not observation.has_lot_address
        )
        coordinate_pair += observation.coordinate_status in {
            "valid_seoul",
            "outside_seoul",
            "invalid",
        }
        coordinate_partial += observation.coordinate_status == "partial"
        coordinate_missing += observation.coordinate_status == "missing"
        target_valid_seoul += observation.coordinate_status == "valid_seoul"
        target_invalid_coordinate += observation.coordinate_status == "invalid"
        target_outside_seoul += observation.coordinate_status == "outside_seoul"
    venue_area_profile = VenueAreaProfile(
        profile_version=SEOUL_REFRESHMENT_PERMIT_AREA_PROFILE_VERSION,
        target_business_type=SEOUL_REFRESHMENT_PERMIT_AREA_TARGET_BUSINESS_TYPE,
        target_state="exact source-reported open predicate",
        unique_target_row_count=venue_target_count,
        facility_total_scope=_finalize_decimal_field(facility_scope),
        site_area=_finalize_decimal_field(site_area),
        both_nonblank_count=both_nonblank,
        both_numeric_count=both_numeric,
        exact_decimal_equal_count=exact_equal,
        exact_decimal_different_count=exact_different,
        road_address_nonblank_count=road_address,
        lot_address_nonblank_count=lot_address,
        any_address_nonblank_count=any_address,
        both_addresses_nonblank_count=both_addresses,
        missing_address_count=missing_address,
        coordinate_pair_nonblank_count=coordinate_pair,
        coordinate_partial_count=coordinate_partial,
        coordinate_missing_count=coordinate_missing,
        valid_seoul_coordinate_count=target_valid_seoul,
        invalid_coordinate_count=target_invalid_coordinate,
        outside_seoul_bbox_count=target_outside_seoul,
        row_retention="aggregate-counts-and-distribution-only;no-venue-rows",
    )
    return PermitProfile(
        service=SEOUL_REFRESHMENT_PERMIT_SERVICE,
        total_count=total_count,
        page_count=page_count,
        row_count=row_count,
        unique_management_number_count=len(seen_ids),
        duplicate_row_count=duplicate_rows,
        identical_duplicate_row_count=identical_duplicates,
        conflicting_duplicate_row_count=conflicting_duplicates,
        adjacent_duplicate_row_count=adjacent_duplicates,
        conflicting_duplicate_later_newer_count=conflict_later_newer,
        conflicting_duplicate_earlier_newer_count=conflict_earlier_newer,
        conflicting_duplicate_timestamp_tie_count=conflict_timestamp_tie,
        catalog_gate_passed=(duplicate_rows == 0 and len(seen_ids) == total_count),
        reported_open_count=open_count,
        not_reported_open_count=len(seen_ids) - open_count,
        missing_coordinate_count=missing_coordinates,
        invalid_coordinate_count=invalid_coordinates,
        outside_seoul_bbox_count=outside_seoul,
        valid_seoul_coordinate_count=valid_seoul,
        provisional_candidate_types=sorted(candidates),
        provisional_open_candidate_count=candidate_total,
        provisional_open_candidate_with_valid_coordinate_count=candidate_valid_seoul,
        status_counts=_sorted_counts(status_counts),
        category_counts=_sorted_counts(category_counts),
        open_category_counts=_sorted_counts(open_category_counts),
        provisional_open_candidate_counts=_sorted_counts(candidate_counts),
        conflicting_duplicate_field_counts=_sorted_counts(conflicting_field_counts),
        open_coffee_shop_venue_area=venue_area_profile,
    )


def write_profile(path: Path, profile: PermitProfile) -> None:
    """Atomically create one aggregate report and refuse every overwrite."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing profile: {path}")
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(
            asdict(profile), temporary, ensure_ascii=False, indent=2, sort_keys=True
        )
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    try:
        with temporary_path.open("rb") as source, path.open("xb") as destination:
            destination.write(source.read())
    finally:
        temporary_path.unlink(missing_ok=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=SEOUL_REFRESHMENT_PERMIT_PROFILE_PATH
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="publish aggregate profile (default: dry-run)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    secret = get_settings().seoul_api_key
    if secret is None or not secret.get_secret_value().strip():
        print("profile failed: missing SEOUL_API_KEY", file=sys.stderr)
        return 2
    try:
        with SeoulRefreshmentPermitClient(secret.get_secret_value()) as client:
            profile = build_permit_profile(client.fetch_page)
        if args.apply:
            write_profile(args.output, profile)
    except Exception as exc:
        print(f"profile failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    if args.apply:
        print(f"profile created: {args.output}")
    else:
        print(
            json.dumps(
                asdict(profile),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        print("dry-run: pass --apply to publish", file=sys.stderr)
    print(
        "rows={rows} open={open_rows} provisional_candidates={candidates}".format(
            rows=profile.row_count,
            open_rows=profile.reported_open_count,
            candidates=profile.provisional_open_candidate_count,
        )
    )
    if not profile.catalog_gate_passed:
        print(
            "catalog gate failed: duplicate or missing management numbers",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
