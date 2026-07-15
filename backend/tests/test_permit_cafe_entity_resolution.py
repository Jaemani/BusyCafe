from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from math import nan

import pytest

from app.config import (
    PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
    PERMIT_CAFE_ENTITY_MIN_PHONE_DIGITS,
)
from app.ingest.permit_cafe_entity_resolution import (
    CafeEntityRecord,
    PermitEntityRecord,
    normalize_entity_text,
    normalize_phone_digits,
    resolve_permit_to_cafes,
)


LATITUDE = 37.55
LONGITUDE = 126.98


def _north(metres: float) -> float:
    return LATITUDE + metres / 111_195.0


def permit(**overrides: object) -> PermitEntityRecord:
    values: dict[str, object] = {
        "permit_id": "permit-1",
        "name": "Busy Café",
        "address": "서울특별시 성동구 연무장길 １２",
        "phone": "02-1234-5678",
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "coordinate_unit": "wgs84_degrees",
        "coordinate_unit_verified": True,
    }
    values.update(overrides)
    return PermitEntityRecord(**values)  # type: ignore[arg-type]


def cafe(identifier: str = "cafe-1", **overrides: object) -> CafeEntityRecord:
    values: dict[str, object] = {
        "cafe_id": identifier,
        "name": "Ｂｕｓｙ   Café",
        "address": " 서울특별시 성동구 연무장길 12 ",
        "phone": "0212345678",
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "coordinate_unit": "wgs84_degrees",
        "coordinate_unit_verified": True,
    }
    values.update(overrides)
    return CafeEntityRecord(**values)  # type: ignore[arg-type]


def test_nfkc_normalization_is_exact_and_conservative() -> None:
    assert normalize_entity_text(" Ｂｕｓｙ　 Café ") == "busy café"
    assert normalize_entity_text("Busy-Café") == "busy-café"
    assert normalize_entity_text("Busy Café") == "busy café"
    assert normalize_entity_text("Busy-Café") != normalize_entity_text("Busy Café")
    assert normalize_phone_digits(" +82 (2) 1234-5678 ") == "82212345678"


def test_exact_address_name_and_distance_produce_one_verified_match() -> None:
    result = resolve_permit_to_cafes(permit(phone=None), [cafe(phone=None)])

    assert result.status == "verified"
    assert result.verified_cafe_id == "cafe-1"
    assert result.abstained is False
    assert result.strong_candidate_count == 1
    evidence = result.strong_candidates[0]
    assert evidence.normalized_address_exact is True
    assert evidence.normalized_name_exact is True
    assert evidence.normalized_phone_exact is False
    assert evidence.within_distance_threshold is True
    assert evidence.distance_m == pytest.approx(0.0)


def test_exact_address_phone_can_verify_when_name_differs() -> None:
    result = resolve_permit_to_cafes(
        permit(name="인허가 상호"), [cafe(name="지도 상호")]
    )

    assert result.status == "verified"
    evidence = result.strong_candidates[0]
    assert evidence.normalized_name_exact is False
    assert evidence.normalized_phone_exact is True


@pytest.mark.parametrize(
    "candidate",
    [
        cafe(address="서울특별시 성동구 다른길 12"),
        cafe(latitude=_north(PERMIT_CAFE_ENTITY_MAX_DISTANCE_M + 1)),
        cafe(name="Busy Cafe Annex", phone=None),
    ],
    ids=["address-mismatch", "too-far", "no-name-or-phone-match"],
)
def test_any_missing_strong_condition_returns_missing(
    candidate: CafeEntityRecord,
) -> None:
    source = permit(phone=None) if candidate.phone is None else permit()
    result = resolve_permit_to_cafes(source, [candidate])

    assert result.status == "missing"
    assert result.verified_cafe_id is None
    assert result.abstained is True
    assert result.strong_candidate_count == 0
    assert result.strong_candidates == ()


def test_two_strong_candidates_are_ambiguous_and_abstain_deterministically() -> None:
    candidates = [
        cafe("cafe-b", latitude=_north(20)),
        cafe("cafe-a", latitude=_north(10)),
    ]

    forward = resolve_permit_to_cafes(permit(), candidates)
    reverse = resolve_permit_to_cafes(permit(), list(reversed(candidates)))

    assert forward == reverse
    assert forward.status == "ambiguous"
    assert forward.verified_cafe_id is None
    assert forward.abstained is True
    assert forward.strong_candidate_count == 2
    assert [item.cafe_id for item in forward.strong_candidates] == [
        "cafe-a",
        "cafe-b",
    ]
    assert [item.distance_m for item in forward.strong_candidates] == pytest.approx(
        [10, 20], abs=0.1
    )


def test_zero_candidates_is_missing_and_abstains() -> None:
    result = resolve_permit_to_cafes(permit(), [])

    assert result.status == "missing"
    assert result.abstained is True
    assert result.strong_candidates == ()


@pytest.mark.parametrize(
    ("source", "candidates"),
    [
        (permit(phone="12345678"), [cafe()]),
        (permit(), [cafe(phone="12345678")]),
    ],
    ids=["permit", "cafe"],
)
def test_nonempty_phone_below_minimum_length_fails_closed(
    source: PermitEntityRecord, candidates: list[CafeEntityRecord]
) -> None:
    with pytest.raises(
        ValueError,
        match=f"at least {PERMIT_CAFE_ENTITY_MIN_PHONE_DIGITS} digits",
    ):
        resolve_permit_to_cafes(source, candidates)


def test_phone_with_non_separator_text_fails_closed() -> None:
    with pytest.raises(ValueError, match="common separators"):
        resolve_permit_to_cafes(permit(phone="02-1234-5678 ext 1"), [cafe()])


@pytest.mark.parametrize(
    ("source", "candidate", "message"),
    [
        (permit(coordinate_unit_verified=False), cafe(), "permit.*verified"),
        (permit(coordinate_unit="metres"), cafe(), "permit.*wgs84"),
        (permit(latitude=91.0), cafe(), "lat1 must be between"),
        (permit(latitude=nan), cafe(), "permit coordinates must be finite"),
        (
            permit(),
            cafe(coordinate_unit_verified=False),
            "cafe cafe-1.*verified",
        ),
        (permit(), cafe(longitude=181.0), "lng1 must be between"),
    ],
)
def test_invalid_or_unverified_coordinates_fail_closed(
    source: PermitEntityRecord,
    candidate: CafeEntityRecord,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_permit_to_cafes(source, [candidate])


@pytest.mark.parametrize(
    ("distance", "digits", "message"),
    [
        (-1.0, 9, "max_distance_m"),
        (0.0, 9, "max_distance_m"),
        (nan, 9, "max_distance_m"),
        (50.0, 0, "min_phone_digits"),
    ],
)
def test_invalid_tuning_parameters_fail_closed(
    distance: float, digits: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_permit_to_cafes(
            permit(), [cafe()], max_distance_m=distance, min_phone_digits=digits
        )


def test_duplicate_cafe_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="duplicate cafe_id"):
        resolve_permit_to_cafes(permit(), [cafe(), cafe()])


def test_records_evidence_and_result_are_frozen() -> None:
    source = permit()
    candidate = cafe()
    result = resolve_permit_to_cafes(source, [candidate])

    with pytest.raises(FrozenInstanceError):
        source.name = "change"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.name = "change"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = "missing"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        replace(result.strong_candidates[0], distance_m=1.0).distance_m = 2.0  # type: ignore[misc]
