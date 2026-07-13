from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest.seoul_refreshment_candidates import (
    normalize_phone,
    resolve_permit_candidates,
    select_review_sample,
)
from app.schemas import SeoulRefreshmentPermit


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "seoul_refreshment_permits_sample.json"
)


def _rows() -> list[SeoulRefreshmentPermit]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [
        SeoulRefreshmentPermit.model_validate(row)
        for row in payload["LOCALDATA_072405"]["row"]
    ]


def test_resolver_keeps_only_open_exact_categories_with_seoul_coordinates() -> None:
    resolution = resolve_permit_candidates(_rows())

    assert len(resolution.candidates) == 1
    candidate = resolution.candidates[0]
    assert candidate.name == "스타벅스 서초우성사거리점"
    assert candidate.category == "커피숍"
    assert candidate.latitude == pytest.approx(37.4935091, abs=1e-6)
    assert candidate.longitude == pytest.approx(127.0292067, abs=1e-6)
    assert resolution.exclusion_reason_counts == {
        "category_not_selected": 1,
        "not_reported_open": 1,
    }


def test_exact_duplicates_collapse_by_management_number() -> None:
    active = _rows()[0]
    resolution = resolve_permit_candidates([active, active.model_copy(deep=True)])

    assert len(resolution.candidates) == 1
    assert resolution.unique_management_number_count == 1
    assert resolution.exact_duplicate_row_count == 1
    assert resolution.quarantined_group_count == 0


def test_phone_only_variants_keep_candidate_and_fail_phone_closed() -> None:
    active = _rows()[0].model_copy(update={"phone": "02-1234-5678"})
    same_normalized = active.model_copy(update={"phone": "02 1234 5678"})
    disagreement = active.model_copy(update={"phone": "02-9999-0000"})

    agreed = resolve_permit_candidates([active, same_normalized])
    conflicted = resolve_permit_candidates([active, disagreement])

    assert agreed.candidates[0].phone == "0212345678"
    assert agreed.phone_variant_group_count == 1
    assert agreed.phone_conflict_group_count == 0
    assert conflicted.candidates[0].phone is None
    assert conflicted.phone_conflict_group_count == 1
    assert conflicted.quarantined_group_count == 0
    assert normalize_phone(" +82 (2) 1234-5678 ") == "82212345678"


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"business_name": "다른 카페"}, "name_conflict"),
        ({"trade_status_name": "폐업"}, "status_conflict"),
        ({"business_type": "편의점"}, "category_conflict"),
        ({"road_address": "다른 주소"}, "address_conflict"),
        ({"projected_x_m": 210_000.0}, "coordinate_conflict"),
    ],
)
def test_identity_conflicts_quarantine_whole_group(
    updates: dict[str, object], reason: str
) -> None:
    active = _rows()[0]
    resolution = resolve_permit_candidates(
        [active, active.model_copy(update=updates)]
    )

    assert resolution.candidates == ()
    assert resolution.quarantined_group_count == 1
    assert resolution.quarantine_reason_counts[reason] == 1


def test_missing_id_and_bad_coordinates_are_excluded_without_guessing() -> None:
    active = _rows()[0]
    missing_id = active.model_copy(update={"management_number": None})
    missing_xy = active.model_copy(
        update={"management_number": "missing-xy", "projected_x_m": None}
    )
    outside = active.model_copy(
        update={
            "management_number": "outside",
            "projected_x_m": 100_000.0,
            "projected_y_m": 100_000.0,
        }
    )

    resolution = resolve_permit_candidates([missing_id, missing_xy, outside])

    assert resolution.candidates == ()
    assert resolution.quarantine_reason_counts == {"missing_management_number": 1}
    assert resolution.exclusion_reason_counts == {
        "missing_coordinates": 1,
        "outside_seoul_bbox": 1,
    }


def test_review_sample_is_hash_stable_and_category_distributed() -> None:
    active = _rows()[0]
    rows = []
    for index, category in enumerate(("커피숍", "다방", "전통찻집", "떡카페")):
        for offset in range(3):
            rows.append(
                active.model_copy(
                    update={
                        "management_number": f"{index}-{offset}",
                        "business_name": f"카페 {index}-{offset}",
                        "business_type": category,
                        "hygiene_type": category,
                    }
                )
            )
    candidates = resolve_permit_candidates(reversed(rows)).candidates

    first = select_review_sample(candidates, 8)
    second = select_review_sample(tuple(reversed(candidates)), 8)

    assert first == second
    assert {candidate.category for candidate in first[:4]} == {
        "커피숍",
        "다방",
        "전통찻집",
        "떡카페",
    }
