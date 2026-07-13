from __future__ import annotations

from app.ingest.permit_reconciliation import (
    CatalogPlace,
    normalize_name,
    reconcile_candidates,
    select_unmatched_review_sample,
)
from app.ingest.seoul_refreshment_candidates import PlaceCandidate


def _candidate(identifier: str, **overrides: object) -> PlaceCandidate:
    values: dict[str, object] = {
        "source": "seoul_refreshment_permits",
        "source_id": identifier,
        "name": f"카페 {identifier}",
        "latitude": 37.55,
        "longitude": 126.98,
        "category": "커피숍",
        "road_address": None,
        "lot_address": None,
        "phone": None,
    }
    values.update(overrides)
    return PlaceCandidate(**values)  # type: ignore[arg-type]


def _catalog(identifier: str, **overrides: object) -> CatalogPlace:
    values: dict[str, object] = {
        "catalog_id": identifier,
        "name": f"카페 {identifier}",
        "latitude": 37.55,
        "longitude": 126.98,
        "category": "cafe",
        "phone": None,
    }
    values.update(overrides)
    return CatalogPlace(**values)  # type: ignore[arg-type]


def _north(metres: float) -> float:
    return 37.55 + metres / 111_195.0


def test_normalized_exact_name_and_phone_rules_are_conservative() -> None:
    candidates = [
        _candidate("name", name="카페-봄", latitude=_north(40)),
        _candidate("phone", name="다른 상호", phone="02-1234-5678", latitude=_north(120)),
        _candidate("both", name="BOTH CAFE", phone="010 1111 2222"),
        _candidate("fuzzy", name="카페보미", longitude=127.1),
        _candidate("far-name", name="먼 카페", latitude=_north(51)),
    ]
    catalog = [
        _catalog("name", name="카페 봄"),
        _catalog("phone", name="원장 상호", phone="0212345678"),
        _catalog("both", name="both cafe", phone="010-1111-2222"),
        _catalog("fuzzy", name="카페봄", longitude=127.1),
        _catalog("far-name", name="먼 카페"),
    ]

    result = reconcile_candidates(candidates, catalog)

    assert result.match_rule_counts == {
        "exact_name": 1,
        "exact_name_and_phone": 1,
        "exact_phone": 1,
    }
    assert {value.source_id for value in result.unmatched} == {"fuzzy", "far-name"}
    assert normalize_name(" Ｂｕｓｙ-Café ") == "busycafé"


def test_multiple_catalog_matches_and_reverse_collisions_are_ambiguous() -> None:
    duplicate_name = _candidate("one", name="같은 카페")
    shared_a = _candidate("two-a", name="공동 카페")
    shared_b = _candidate("two-b", name="공동 카페")
    catalog = [
        _catalog("one-a", name="같은 카페"),
        _catalog("one-b", name="같은 카페", latitude=_north(20)),
        _catalog("shared", name="공동 카페"),
    ]

    result = reconcile_candidates([duplicate_name, shared_a, shared_b], catalog)

    assert result.matches == ()
    assert {value.source_id for value in result.ambiguous} == {
        "one",
        "two-a",
        "two-b",
    }


def test_grid_index_avoids_cartesian_distance_checks() -> None:
    catalog = [
        _catalog(
            str(index),
            name="같은 카페",
            latitude=37.41 + (index // 100) * 0.002,
            longitude=126.76 + (index % 100) * 0.002,
        )
        for index in range(2_000)
    ]
    candidate = _candidate(
        "target",
        name="같은 카페",
        latitude=catalog[0].latitude,
        longitude=catalog[0].longitude,
    )

    result = reconcile_candidates([candidate], catalog)

    assert result.distance_check_count < 20
    assert result.distance_check_count < len(catalog)


def test_unmatched_sample_is_hash_stable_and_category_distributed() -> None:
    candidates = [
        _candidate(f"{category}-{index}", category=category)
        for category in ("커피숍", "다방", "전통찻집", "떡카페")
        for index in range(3)
    ]

    first = select_unmatched_review_sample(candidates, 8)
    second = select_unmatched_review_sample(tuple(reversed(candidates)), 8)

    assert first == second
    assert len({candidate.category for candidate in first[:4]}) == 4


def test_duplicate_candidate_identity_is_rejected() -> None:
    candidate = _candidate("duplicate")

    try:
        reconcile_candidates([candidate, candidate], [])
    except ValueError as exc:
        assert "duplicate candidate source_id" in str(exc)
    else:
        raise AssertionError("duplicate source identity must fail closed")
