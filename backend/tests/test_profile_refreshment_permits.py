from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.clients.seoul_refreshment_permits import parse_permit_page
from scripts.profile_refreshment_permits import (
    PermitProfileError,
    _parse_args,
    build_permit_profile,
    write_profile,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "seoul_refreshment_permits_sample.json"
)


def fixture_page():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["LOCALDATA_072405"]["list_total_count"] = 3
    return parse_permit_page(payload)


def test_build_profile_aggregates_without_retaining_business_rows() -> None:
    page = fixture_page()
    profile = build_permit_profile(
        lambda start, end: page,
        candidate_types=("커피숍",),
    )

    assert profile.total_count == profile.row_count == 3
    assert profile.unique_management_number_count == 3
    assert profile.duplicate_row_count == 0
    assert profile.identical_duplicate_row_count == 0
    assert profile.conflicting_duplicate_row_count == 0
    assert profile.conflicting_duplicate_field_counts == {}
    assert profile.catalog_gate_passed is True
    assert profile.page_count == 1
    assert profile.reported_open_count == 2
    assert profile.not_reported_open_count == 1
    assert profile.category_counts == {"커피숍": 2, "편의점": 1}
    assert profile.open_category_counts == {"커피숍": 1, "편의점": 1}
    assert profile.provisional_open_candidate_counts == {"커피숍": 1}
    assert profile.provisional_open_candidate_count == 1
    assert profile.provisional_open_candidate_with_valid_coordinate_count == 1
    assert profile.valid_seoul_coordinate_count == 3
    assert profile.missing_coordinate_count == 0
    venue = profile.open_coffee_shop_venue_area
    assert venue.unique_target_row_count == 1
    assert venue.facility_total_scope.numeric_count == 1
    assert venue.facility_total_scope.p50 == "125.50000"
    assert venue.site_area.p50 == "125.50"
    assert venue.both_numeric_count == 1
    assert venue.exact_decimal_equal_count == 1
    assert venue.facility_total_scope.unit == "m2"
    assert (
        venue.facility_total_scope.unit_status
        == "verified_official_administrative_meaning"
    )
    assert "nttId=1011" in venue.facility_total_scope.unit_provenance
    assert venue.row_retention == "aggregate-counts-and-distribution-only;no-venue-rows"


def test_open_coffee_shop_area_profile_is_decimal_exact_and_order_independent() -> None:
    base = fixture_page().rows[0].model_dump(mode="json", by_alias=True)
    pairs = (
        ("0", "0"),
        ("-1", "-2"),
        ("10", "10"),
        ("20", "25"),
        ("30", "not-numeric"),
        ("1000000", "1000000"),
        ("", ""),
    )
    rows = []
    for index, (facility_raw, site_raw) in enumerate(pairs):
        row = dict(base)
        row.update(
            {
                "MGTNO": f"test-{index}",
                "BPLCNM": f"test venue {index}",
                "FACILTOTSCP": facility_raw,
                "SITEAREA": site_raw,
            }
        )
        if index == 1:
            row["RDNWHLADDR"] = ""
        elif index == 2:
            row["SITEWHLADDR"] = ""
            row["X"] = ""
            row["Y"] = ""
        elif index == 3:
            row["RDNWHLADDR"] = ""
            row["SITEWHLADDR"] = ""
            row["X"] = ""
        rows.append(row)

    def page_for(source_rows):
        return parse_permit_page(
            {
                "LOCALDATA_072405": {
                    "list_total_count": len(source_rows),
                    "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"},
                    "row": source_rows,
                }
            }
        )

    first = build_permit_profile(lambda _start, _end: page_for(rows))
    second = build_permit_profile(lambda _start, _end: page_for(list(reversed(rows))))
    assert first.open_coffee_shop_venue_area == second.open_coffee_shop_venue_area

    venue = first.open_coffee_shop_venue_area
    facility = venue.facility_total_scope
    assert venue.unique_target_row_count == 7
    assert facility.nonblank_count == 6
    assert facility.numeric_count == 6
    assert facility.nonnumeric_count == 0
    assert facility.zero_count == 1
    assert facility.negative_count == 1
    assert facility.extreme_count == 1
    assert facility.extreme_abs_threshold == "10000"
    assert (facility.minimum, facility.p1, facility.p5) == ("-1", "-1", "-1")
    assert facility.p50 == "10"
    assert (facility.p95, facility.p99, facility.maximum) == (
        "1000000",
        "1000000",
        "1000000",
    )
    site = venue.site_area
    assert site.nonblank_count == 6
    assert site.numeric_count == 5
    assert site.nonnumeric_count == 1
    assert site.minimum == "-2"
    assert site.p50 == "10"
    assert venue.both_nonblank_count == 6
    assert venue.both_numeric_count == 5
    assert venue.exact_decimal_equal_count == 3
    assert venue.exact_decimal_different_count == 2
    assert venue.road_address_nonblank_count == 5
    assert venue.lot_address_nonblank_count == 5
    assert venue.any_address_nonblank_count == 6
    assert venue.both_addresses_nonblank_count == 4
    assert venue.missing_address_count == 1
    assert venue.coordinate_pair_nonblank_count == 5
    assert venue.coordinate_partial_count == 1
    assert venue.coordinate_missing_count == 1
    assert venue.valid_seoul_coordinate_count == 5


def test_profile_counts_duplicates_and_fails_catalog_gate() -> None:
    page = fixture_page()
    payload = {
        "LOCALDATA_072405": {
            "list_total_count": 2,
            "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"},
            "row": [
                page.rows[0].model_dump(by_alias=True),
                page.rows[0].model_dump(by_alias=True),
            ],
        }
    }
    duplicate_page = parse_permit_page(payload)

    profile = build_permit_profile(lambda start, end: duplicate_page)

    assert profile.row_count == 2
    assert profile.unique_management_number_count == 1
    assert profile.duplicate_row_count == 1
    assert profile.identical_duplicate_row_count == 1
    assert profile.conflicting_duplicate_row_count == 0
    assert profile.adjacent_duplicate_row_count == 1
    assert profile.catalog_gate_passed is False


def test_area_profile_selects_latest_conflicting_duplicate_independent_of_order() -> (
    None
):
    base = fixture_page().rows[0].model_dump(mode="json", by_alias=True)
    older = {
        **base,
        "MGTNO": "duplicate-area",
        "UPDATEDT": "2026-01-01 00:00:00",
        "FACILTOTSCP": "10",
        "SITEAREA": "10",
    }
    newer = {
        **base,
        "MGTNO": "duplicate-area",
        "UPDATEDT": "2026-02-01 00:00:00",
        "FACILTOTSCP": "20",
        "SITEAREA": "20",
    }

    def profile(source_rows):
        page = parse_permit_page(
            {
                "LOCALDATA_072405": {
                    "list_total_count": 2,
                    "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"},
                    "row": source_rows,
                }
            }
        )
        return build_permit_profile(lambda _start, _end: page)

    forward = profile([older, newer]).open_coffee_shop_venue_area
    reverse = profile([newer, older]).open_coffee_shop_venue_area
    assert forward == reverse
    assert forward.unique_target_row_count == 1
    assert forward.facility_total_scope.p50 == "20"


def test_profile_fails_closed_when_total_changes_between_pages() -> None:
    page = fixture_page()
    first = page.model_copy(update={"total_count": 4, "rows": page.rows[:2]})
    second = page.model_copy(update={"total_count": 5, "rows": page.rows[2:]})

    def fetch(start: int, end: int):
        return first if start == 1 else second

    with pytest.raises(PermitProfileError, match="source total changed"):
        build_permit_profile(fetch, page_size=2, candidate_types=("커피숍",))


def test_write_profile_is_deterministic_and_refuses_overwrite(tmp_path: Path) -> None:
    profile = build_permit_profile(
        lambda start, end: fixture_page(),
        candidate_types=("커피숍",),
    )
    destination = tmp_path / "profile.json"

    write_profile(destination, profile)
    first = destination.read_text(encoding="utf-8")
    assert "스타벅스" not in first
    assert "3210000-104-2017-00481" not in first
    assert json.loads(first)["row_count"] == 3
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_profile(destination, profile)


def test_profile_cli_is_dry_run_by_default() -> None:
    assert _parse_args([]).apply is False
    assert _parse_args(["--apply"]).apply is True
