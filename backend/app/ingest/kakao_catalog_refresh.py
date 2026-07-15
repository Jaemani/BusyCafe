"""Pure planning for Kakao-first canonical cafe display refreshes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.config import (
    KAKAO_CAFE_CATEGORY_CODE,
    KAKAO_CATALOG_REFRESH_LARGE_MOVE_M,
    KAKAO_CATALOG_REFRESH_MOVE_BUCKETS_M,
)
from app.geo import haversine_m
from app.ingest.kakao_catalog_expansion import is_target_seoul_kakao_place
from app.schemas import KakaoPlace


KAKAO_SOURCE_PRIMARY_MATCH_METHOD = "source_primary"
KAKAO_SAFE_DISPLAY_REFRESH_MATCH_METHODS = frozenset(
    {
        KAKAO_SOURCE_PRIMARY_MATCH_METHOD,
        "exact_name_and_phone",
        "exact_name_and_address",
        "exact_phone_and_address",
        "exact_name_and_phone_and_address",
    }
)


@dataclass(frozen=True, slots=True)
class KakaoRefreshTarget:
    cafe_id: int
    origin_provider: str
    kakao_place_id: str
    match_method: str
    name: str
    latitude: float
    longitude: float
    road_address: str | None
    phone: str | None
    source_release: str


@dataclass(frozen=True, slots=True)
class KakaoDisplayRefresh:
    cafe_id: int
    kakao_place_id: str
    origin_provider: str
    name: str
    latitude: float
    longitude: float
    road_address: str | None
    phone: str | None
    source_release: str
    category: str
    lot_address: str | None
    direct_url: str
    changed_fields: tuple[str, ...]
    movement_m: float


@dataclass(frozen=True, slots=True)
class KakaoRefreshReport:
    eligible_target_count: int
    seen_target_count: int
    missing_target_count: int
    rejected_target_count: int
    changed_target_count: int
    coordinate_changed_count: int
    large_move_count: int
    move_bucket_counts: dict[str, int]
    changed_field_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class KakaoRefreshBuild:
    refreshes: tuple[KakaoDisplayRefresh, ...]
    seen_cafe_ids: tuple[int, ...]
    report: KakaoRefreshReport


def _move_bucket(distance_m: float, buckets_m: Sequence[float]) -> str:
    lower = 0.0
    for upper in buckets_m:
        if distance_m <= upper:
            return f"gt_{lower:g}_lte_{upper:g}m"
        lower = upper
    return f"gt_{lower:g}m"


def build_kakao_display_refresh(
    places: Sequence[KakaoPlace],
    targets: Sequence[KakaoRefreshTarget],
    *,
    source_release: str,
    large_move_m: float = KAKAO_CATALOG_REFRESH_LARGE_MOVE_M,
    move_buckets_m: Sequence[float] = KAKAO_CATALOG_REFRESH_MOVE_BUCKETS_M,
) -> KakaoRefreshBuild:
    """Plan display-field updates without mutating database state."""

    if not source_release:
        raise ValueError("source_release must not be empty")
    if large_move_m <= 0:
        raise ValueError("large_move_m must be > 0")
    if (
        not move_buckets_m
        or any(value <= 0 for value in move_buckets_m)
        or tuple(move_buckets_m) != tuple(sorted(set(move_buckets_m)))
    ):
        raise ValueError("move_buckets_m must be unique, positive, and sorted")

    places_by_id: dict[str, KakaoPlace] = {}
    rejected_place_ids: set[str] = set()
    for place in places:
        if place.category_group_code != KAKAO_CAFE_CATEGORY_CODE:
            raise ValueError(f"Kakao place is not CE7: {place.place_id}")
        if place.place_id in places_by_id or place.place_id in rejected_place_ids:
            raise ValueError(f"duplicate Kakao place ID: {place.place_id}")
        if is_target_seoul_kakao_place(place):
            places_by_id[place.place_id] = place
        else:
            rejected_place_ids.add(place.place_id)

    targets_by_cafe: dict[int, KakaoRefreshTarget] = {}
    target_place_ids: set[str] = set()
    for target in targets:
        if target.cafe_id in targets_by_cafe:
            raise ValueError(f"duplicate cafe refresh target: {target.cafe_id}")
        if target.kakao_place_id in target_place_ids:
            raise ValueError(
                f"duplicate Kakao refresh ownership: {target.kakao_place_id}"
            )
        if target.match_method not in KAKAO_SAFE_DISPLAY_REFRESH_MATCH_METHODS:
            raise ValueError(
                f"unsafe Kakao refresh match method: {target.match_method}"
            )
        if (
            target.match_method == KAKAO_SOURCE_PRIMARY_MATCH_METHOD
            and target.origin_provider != "kakao"
        ):
            raise ValueError(
                "Kakao source_primary refresh requires a Kakao-origin cafe"
            )
        if (
            target.origin_provider == "kakao"
            and target.match_method != KAKAO_SOURCE_PRIMARY_MATCH_METHOD
        ):
            raise ValueError(
                "Kakao-origin refresh requires source_primary ownership"
            )
        targets_by_cafe[target.cafe_id] = target
        target_place_ids.add(target.kakao_place_id)

    refreshes: list[KakaoDisplayRefresh] = []
    seen_cafe_ids: list[int] = []
    missing_target_count = rejected_target_count = coordinate_changed_count = 0
    changed_fields = Counter[str]()
    move_buckets = Counter[str]()
    large_move_count = 0
    for cafe_id in sorted(targets_by_cafe):
        target = targets_by_cafe[cafe_id]
        place = places_by_id.get(target.kakao_place_id)
        if place is None:
            missing_target_count += 1
            if target.kakao_place_id in rejected_place_ids:
                rejected_target_count += 1
            continue
        seen_cafe_ids.append(cafe_id)
        road_address = place.road_address_name or place.address_name or None
        phone = place.phone or None
        values: dict[str, object] = {
            "name": place.place_name,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "road_address": road_address,
            "phone": phone,
        }
        current: dict[str, object] = {
            "name": target.name,
            "latitude": target.latitude,
            "longitude": target.longitude,
            "road_address": target.road_address,
            "phone": target.phone,
        }
        if target.origin_provider == "kakao":
            values["source_release"] = source_release
            current["source_release"] = target.source_release
        changed = tuple(field for field in values if values[field] != current[field])
        if not changed:
            continue
        changed_fields.update(changed)
        movement_m = haversine_m(
            target.latitude,
            target.longitude,
            place.latitude,
            place.longitude,
        )
        if "latitude" in changed or "longitude" in changed:
            coordinate_changed_count += 1
            move_buckets[_move_bucket(movement_m, move_buckets_m)] += 1
            if movement_m > large_move_m:
                large_move_count += 1
        refreshes.append(
            KakaoDisplayRefresh(
                cafe_id=target.cafe_id,
                kakao_place_id=target.kakao_place_id,
                origin_provider=target.origin_provider,
                name=place.place_name,
                latitude=place.latitude,
                longitude=place.longitude,
                road_address=road_address,
                phone=phone,
                source_release=source_release,
                category=place.category_name or place.category_group_code,
                lot_address=place.address_name or None,
                direct_url=f"https://place.map.kakao.com/{place.place_id}",
                changed_fields=changed,
                movement_m=movement_m,
            )
        )

    return KakaoRefreshBuild(
        refreshes=tuple(refreshes),
        seen_cafe_ids=tuple(seen_cafe_ids),
        report=KakaoRefreshReport(
            eligible_target_count=len(targets),
            seen_target_count=len(seen_cafe_ids),
            missing_target_count=missing_target_count,
            rejected_target_count=rejected_target_count,
            changed_target_count=len(refreshes),
            coordinate_changed_count=coordinate_changed_count,
            large_move_count=large_move_count,
            move_bucket_counts=dict(sorted(move_buckets.items())),
            changed_field_counts=dict(sorted(changed_fields.items())),
        ),
    )
