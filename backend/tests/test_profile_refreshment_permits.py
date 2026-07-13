from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.clients.seoul_refreshment_permits import parse_permit_page
from scripts.profile_refreshment_permits import (
    PermitProfileError,
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
    assert json.loads(first)["row_count"] == 3
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_profile(destination, profile)
