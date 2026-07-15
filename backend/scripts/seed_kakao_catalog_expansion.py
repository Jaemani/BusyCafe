#!/usr/bin/env python3
"""Dry-run Kakao-owned canonical cafe additions; writes require ``--apply``."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    KAKAO_CATALOG_APPLY_ABSOLUTE_MAX_CANDIDATES,
    KAKAO_CATALOG_REFRESH_ABSOLUTE_MAX_LARGE_MOVES,
    KAKAO_CATALOG_REFRESH_LARGE_MOVE_M,
    PROVIDER_VERIFIED_CAFE_CONFIDENCE,
)
from app.database import create_db_engine
from app.ingest.kakao_catalog_expansion import (
    KAKAO_CANONICAL_SOURCE,
    CanonicalCafeIdentity,
    KakaoCanonicalCandidate,
    build_kakao_expansion,
)
from app.ingest.kakao_catalog_refresh import (
    KAKAO_SAFE_DISPLAY_REFRESH_MATCH_METHODS,
    KakaoDisplayRefresh,
    KakaoRefreshTarget,
    build_kakao_display_refresh,
)
from app.models import Cafe, CafeProviderPlace
from app.schemas import KakaoPlace
from scripts.build_provider_cafe_catalog import read_kakao_cache
from scripts.cache_kakao_cafes import DEFAULT_CACHE, manifest_path_for


KAKAO_SOURCE_MATCH_METHOD = "source_primary"
KAKAO_LARGE_MOVE_QUARANTINE_REASON = (
    "large_move_batch_exceeds_allowed_bound"
)


class KakaoCatalogApplyError(RuntimeError):
    """Raised before publication when catalog ownership is not unambiguous."""


@dataclass(frozen=True, slots=True)
class ValidatedKakaoSnapshot:
    places: tuple[KakaoPlace, ...]
    generated_at: datetime
    source_release: str


@dataclass(frozen=True, slots=True)
class KakaoCatalogApplyReport:
    mode: str
    source_release: str
    cache_place_count: int
    outside_target_region_count: int
    outside_target_bbox_count: int
    canonical_cafe_count: int
    existing_kakao_origin_count: int
    existing_kakao_provider_count: int
    existing_provider_id_missing_from_cache_count: int
    candidate_count: int
    conflict_count: int
    blocking_conflict_count: int
    advisory_conflict_count: int
    conflict_rule_counts: dict[str, int]
    refresh_eligible_count: int
    refresh_seen_count: int
    refresh_missing_count: int
    refresh_rejected_count: int
    planned_cafe_refresh_count: int
    refreshed_cafe_count: int
    refresh_coordinate_changed_count: int
    refresh_large_move_count: int
    refresh_large_move_planned_count: int
    refresh_large_move_applied_count: int
    refresh_large_move_quarantined_count: int
    refresh_large_move_quarantine_reason: str | None
    refresh_move_bucket_counts: dict[str, int]
    refresh_changed_field_counts: dict[str, int]
    refresh_large_move_sample: tuple[dict[str, object], ...]
    planned_cafe_insert_count: int
    planned_provider_insert_count: int
    inserted_cafe_count: int
    inserted_provider_count: int
    candidate_ids_sha256: str
    max_expected_candidates: int | None
    max_expected_large_moves: int | None


def read_validated_kakao_snapshot(
    cache_path: Path,
    manifest_path: Path,
) -> ValidatedKakaoSnapshot:
    """Read one immutable, manifest-verified cache snapshot and release time."""

    try:
        manifest_before = manifest_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"invalid or missing Kakao manifest: {manifest_path}") from exc
    places = read_kakao_cache(cache_path, manifest_path)
    try:
        manifest_after = manifest_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Kakao manifest disappeared: {manifest_path}") from exc
    if not hmac.compare_digest(
        hashlib.sha256(manifest_before).digest(),
        hashlib.sha256(manifest_after).digest(),
    ):
        raise ValueError("Kakao manifest changed while cache was being read")
    try:
        manifest = json.loads(manifest_before)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Kakao manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Kakao manifest must be a JSON object")
    generated_value = manifest.get("generated_at")
    if not isinstance(generated_value, str):
        raise ValueError("Kakao manifest generated_at must be an ISO timestamp")
    try:
        generated_at = datetime.fromisoformat(generated_value)
    except ValueError as exc:
        raise ValueError("Kakao manifest generated_at must be an ISO timestamp") from exc
    if generated_at.tzinfo is None:
        raise ValueError("Kakao manifest generated_at must include a timezone")
    generated_at = generated_at.astimezone(UTC)
    source_release = generated_at.isoformat()
    if len(source_release) > 32:
        raise ValueError("Kakao manifest generated_at exceeds source_release storage")
    return ValidatedKakaoSnapshot(
        places=places,
        generated_at=generated_at,
        source_release=source_release,
    )


def _candidate_values(
    candidate: KakaoCanonicalCandidate,
    *,
    source_release: str,
) -> dict[str, object]:
    road_address = candidate.road_address or candidate.lot_address
    limits = {
        "origin_source_id": (candidate.canonical_source_id, 255),
        "name": (candidate.name, 255),
        "road_address": (road_address, 500),
        "phone": (candidate.phone, 64),
        "detail_url": (candidate.direct_url, 1_000),
    }
    for field, (value, limit) in limits.items():
        if value is not None and len(value) > limit:
            raise KakaoCatalogApplyError(
                f"Kakao candidate {candidate.canonical_source_id} exceeds {field} limit"
            )
    return {
        "origin_provider": KAKAO_CANONICAL_SOURCE,
        "origin_source_id": candidate.canonical_source_id,
        "overture_id": None,
        "source_release": source_release,
        "source_confidence": PROVIDER_VERIFIED_CAFE_CONFIDENCE,
        "primary_category": "cafe",
        "name": candidate.name,
        "lat": candidate.latitude,
        "lng": candidate.longitude,
        "road_address": road_address,
        "phone": candidate.phone,
        "website": None,
        "source_json": [
            {
                "provider": KAKAO_CANONICAL_SOURCE,
                "provider_place_id": candidate.canonical_source_id,
                "category": candidate.category,
                "road_address": candidate.road_address,
                "lot_address": candidate.lot_address,
                "phone": candidate.phone,
                "direct_url": candidate.direct_url,
            }
        ],
        "external_links_json": None,
        "active": True,
    }


def _validate_database_ownership(
    cafes: tuple[Cafe, ...],
    provider_places: tuple[CafeProviderPlace, ...],
) -> None:
    cafes_by_id = {cafe.id: cafe for cafe in cafes}
    cafes_by_origin = {
        (cafe.origin_provider, cafe.origin_source_id): cafe for cafe in cafes
    }
    providers_by_key = {
        (place.provider, place.provider_place_id): place
        for place in provider_places
    }
    providers_by_cafe_provider = {
        (place.cafe_id, place.provider): place for place in provider_places
    }
    if len(cafes_by_id) != len(cafes) or len(cafes_by_origin) != len(cafes):
        raise KakaoCatalogApplyError("database contains a duplicate cafe origin")
    if (
        len(providers_by_key) != len(provider_places)
        or len(providers_by_cafe_provider) != len(provider_places)
    ):
        raise KakaoCatalogApplyError("database contains a provider ownership collision")
    for place in provider_places:
        if place.cafe_id not in cafes_by_id:
            raise KakaoCatalogApplyError("provider place targets a missing cafe")
    for cafe in cafes:
        if cafe.origin_provider != KAKAO_CANONICAL_SOURCE:
            continue
        place_id = cafe.origin_source_id
        expected_url = f"https://place.map.kakao.com/{place_id}"
        place = providers_by_key.get((KAKAO_CANONICAL_SOURCE, place_id))
        owned_place = providers_by_cafe_provider.get(
            (cafe.id, KAKAO_CANONICAL_SOURCE)
        )
        if (
            place is None
            or owned_place is not place
            or place.cafe_id != cafe.id
            or not place.active
            or place.detail_url != expected_url
            or place.match_method != KAKAO_SOURCE_MATCH_METHOD
        ):
            raise KakaoCatalogApplyError(
                f"Kakao origin/provider collision for place ID {place_id}"
            )


def _refresh_candidate(refresh: KakaoDisplayRefresh) -> KakaoCanonicalCandidate:
    return KakaoCanonicalCandidate(
        canonical_source=KAKAO_CANONICAL_SOURCE,
        canonical_source_id=refresh.kakao_place_id,
        name=refresh.name,
        latitude=refresh.latitude,
        longitude=refresh.longitude,
        category=refresh.category,
        road_address=refresh.road_address,
        lot_address=refresh.lot_address,
        phone=refresh.phone,
        direct_url=refresh.direct_url,
    )


def seed_kakao_catalog_expansion(
    session: Session,
    snapshot: ValidatedKakaoSnapshot,
    *,
    apply: bool,
    max_expected_candidates: int | None,
    max_expected_large_moves: int | None = None,
) -> KakaoCatalogApplyReport:
    """Select against current DB state, then insert cafes and links atomically."""

    if snapshot.generated_at.tzinfo is None:
        raise ValueError("Kakao snapshot generated_at must include a timezone")
    expected_release = snapshot.generated_at.astimezone(UTC).isoformat()
    if snapshot.source_release != expected_release or len(expected_release) > 32:
        raise ValueError("Kakao snapshot source_release does not match generated_at")
    if max_expected_candidates is not None and not (
        0
        <= max_expected_candidates
        <= KAKAO_CATALOG_APPLY_ABSOLUTE_MAX_CANDIDATES
    ):
        raise ValueError(
            "max_expected_candidates must be between 0 and "
            f"{KAKAO_CATALOG_APPLY_ABSOLUTE_MAX_CANDIDATES}"
        )
    if apply and max_expected_candidates is None:
        raise KakaoCatalogApplyError(
            "--apply requires an explicit --max-candidates safety bound"
        )
    if max_expected_large_moves is not None and not (
        0
        <= max_expected_large_moves
        <= KAKAO_CATALOG_REFRESH_ABSOLUTE_MAX_LARGE_MOVES
    ):
        raise ValueError(
            "max_expected_large_moves must be between 0 and "
            f"{KAKAO_CATALOG_REFRESH_ABSOLUTE_MAX_LARGE_MOVES}"
        )
    if apply and max_expected_large_moves is None:
        raise KakaoCatalogApplyError(
            "--apply requires an explicit --max-large-moves safety bound"
        )

    try:
        cafes = tuple(session.scalars(select(Cafe).order_by(Cafe.id)))
        provider_places = tuple(
            session.scalars(select(CafeProviderPlace).order_by(CafeProviderPlace.id))
        )
        _validate_database_ownership(cafes, provider_places)
        cafes_by_id = {cafe.id: cafe for cafe in cafes}
        kakao_places_by_cafe = {
            place.cafe_id: place
            for place in provider_places
            if place.provider == KAKAO_CANONICAL_SOURCE
        }
        existing_kakao_origin_count = sum(
            cafe.origin_provider == KAKAO_CANONICAL_SOURCE for cafe in cafes
        )
        canonical = tuple(
            CanonicalCafeIdentity(
                canonical_id=cafe.id,
                name=cafe.name,
                latitude=cafe.lat,
                longitude=cafe.lng,
                road_address=cafe.road_address,
                phone=cafe.phone,
            )
            for cafe in cafes
        )
        kakao_provider_ids = tuple(
            place.provider_place_id
            for place in provider_places
            if place.provider == KAKAO_CANONICAL_SOURCE
        )
        refresh_targets = tuple(
            KakaoRefreshTarget(
                cafe_id=cafe.id,
                origin_provider=cafe.origin_provider,
                kakao_place_id=kakao_places_by_cafe[cafe.id].provider_place_id,
                match_method=kakao_places_by_cafe[cafe.id].match_method,
                name=cafe.name,
                latitude=cafe.lat,
                longitude=cafe.lng,
                road_address=cafe.road_address,
                phone=cafe.phone,
                source_release=cafe.source_release,
            )
            for cafe in cafes
            if cafe.active
            and cafe.id in kakao_places_by_cafe
            and kakao_places_by_cafe[cafe.id].active
            and kakao_places_by_cafe[cafe.id].match_method
            in KAKAO_SAFE_DISPLAY_REFRESH_MATCH_METHODS
        )
        refresh_build = build_kakao_display_refresh(
            snapshot.places,
            refresh_targets,
            source_release=snapshot.source_release,
        )
        large_move_refreshes = tuple(
            refresh
            for refresh in refresh_build.refreshes
            if refresh.movement_m > KAKAO_CATALOG_REFRESH_LARGE_MOVE_M
        )
        # Preserve the original all-or-none review meaning of the bound. If a
        # batch discovers more suspicious moves than the operator authorized,
        # no arbitrary subset is selected: every large move is quarantined,
        # while normal refreshes and canonical inserts can still publish.
        large_move_batch_allowed = (
            max_expected_large_moves is not None
            and len(large_move_refreshes) <= max_expected_large_moves
        )
        quarantined_large_move_ids = (
            frozenset()
            if large_move_batch_allowed
            else frozenset(refresh.cafe_id for refresh in large_move_refreshes)
        )
        applicable_refreshes = tuple(
            refresh
            for refresh in refresh_build.refreshes
            if refresh.cafe_id not in quarantined_large_move_ids
        )
        planned_large_move_count = (
            len(large_move_refreshes) if large_move_batch_allowed else 0
        )
        large_move_quarantine_reason = (
            KAKAO_LARGE_MOVE_QUARANTINE_REASON
            if quarantined_large_move_ids
            else None
        )
        build = build_kakao_expansion(
            snapshot.places,
            canonical,
            kakao_provider_ids,
        )
        candidates = build.candidates
        if (
            max_expected_candidates is not None
            and len(candidates) > max_expected_candidates
        ):
            raise KakaoCatalogApplyError(
                f"candidate count {len(candidates)} exceeds operator bound "
                f"{max_expected_candidates}"
            )

        origins = {(cafe.origin_provider, cafe.origin_source_id) for cafe in cafes}
        provider_keys = {
            (place.provider, place.provider_place_id) for place in provider_places
        }
        for candidate in candidates:
            origin = (candidate.canonical_source, candidate.canonical_source_id)
            provider_key = (
                KAKAO_CANONICAL_SOURCE,
                candidate.canonical_source_id,
            )
            if origin in origins or provider_key in provider_keys:
                raise KakaoCatalogApplyError(
                    "Kakao candidate collides with an existing origin/provider"
                )
            _candidate_values(candidate, source_release=snapshot.source_release)
        for refresh in applicable_refreshes:
            _candidate_values(
                _refresh_candidate(refresh),
                source_release=snapshot.source_release,
            )

        inserted_cafes = inserted_providers = refreshed_cafes = 0
        if apply:
            for refresh in applicable_refreshes:
                cafe = cafes_by_id[refresh.cafe_id]
                values = _candidate_values(
                    _refresh_candidate(refresh),
                    source_release=snapshot.source_release,
                )
                cafe.name = values["name"]  # type: ignore[assignment]
                cafe.lat = values["lat"]  # type: ignore[assignment]
                cafe.lng = values["lng"]  # type: ignore[assignment]
                cafe.road_address = values["road_address"]  # type: ignore[assignment]
                cafe.phone = values["phone"]  # type: ignore[assignment]
                if cafe.origin_provider == KAKAO_CANONICAL_SOURCE:
                    cafe.source_release = snapshot.source_release
                    cafe.source_json = values["source_json"]  # type: ignore[assignment]
                refreshed_cafes += 1
            for cafe_id in refresh_build.seen_cafe_ids:
                if cafe_id in quarantined_large_move_ids:
                    # Keep suspicious cafe/provider state frozen. Seeing the
                    # same ID at a distant coordinate does not renew identity
                    # verification until that movement is explicitly allowed.
                    continue
                provider = kakao_places_by_cafe[cafe_id]
                provider.detail_url = (
                    f"https://place.map.kakao.com/{provider.provider_place_id}"
                )
                provider.last_seen_at = snapshot.generated_at
                provider.verified_at = snapshot.generated_at
        if apply and candidates:
            created_by_place_id: dict[str, Cafe] = {}
            for candidate in candidates:
                created = Cafe(
                    **_candidate_values(
                        candidate,
                        source_release=snapshot.source_release,
                    )
                )
                created_by_place_id[candidate.canonical_source_id] = created
            session.add_all(created_by_place_id.values())
            session.flush()
            session.add_all(
                CafeProviderPlace(
                    cafe_id=created_by_place_id[candidate.canonical_source_id].id,
                    provider=KAKAO_CANONICAL_SOURCE,
                    provider_place_id=candidate.canonical_source_id,
                    detail_url=candidate.direct_url,
                    active=True,
                    match_method=KAKAO_SOURCE_MATCH_METHOD,
                    match_distance_m=0.0,
                    verified_at=snapshot.generated_at,
                    last_seen_at=snapshot.generated_at,
                )
                for candidate in candidates
            )
            session.flush()
            inserted_cafes = len(candidates)
            inserted_providers = len(candidates)
        if apply:
            session.commit()

        return KakaoCatalogApplyReport(
            mode="write" if apply else "dry-run",
            source_release=snapshot.source_release,
            cache_place_count=len(snapshot.places),
            outside_target_region_count=(
                build.report.outside_target_region_count
            ),
            outside_target_bbox_count=build.report.outside_target_bbox_count,
            canonical_cafe_count=len(cafes),
            existing_kakao_origin_count=existing_kakao_origin_count,
            existing_kakao_provider_count=len(kakao_provider_ids),
            existing_provider_id_missing_from_cache_count=(
                build.report.existing_provider_id_missing_from_cache_count
            ),
            candidate_count=len(candidates),
            conflict_count=build.report.conflict_count,
            blocking_conflict_count=build.report.blocking_conflict_count,
            advisory_conflict_count=build.report.advisory_conflict_count,
            conflict_rule_counts=build.report.conflict_rule_counts,
            refresh_eligible_count=refresh_build.report.eligible_target_count,
            refresh_seen_count=refresh_build.report.seen_target_count,
            refresh_missing_count=refresh_build.report.missing_target_count,
            refresh_rejected_count=refresh_build.report.rejected_target_count,
            planned_cafe_refresh_count=len(applicable_refreshes),
            refreshed_cafe_count=refreshed_cafes,
            refresh_coordinate_changed_count=(
                refresh_build.report.coordinate_changed_count
            ),
            refresh_large_move_count=refresh_build.report.large_move_count,
            refresh_large_move_planned_count=planned_large_move_count,
            refresh_large_move_applied_count=(
                planned_large_move_count if apply else 0
            ),
            refresh_large_move_quarantined_count=len(
                quarantined_large_move_ids
            ),
            refresh_large_move_quarantine_reason=(
                large_move_quarantine_reason
            ),
            refresh_move_bucket_counts=refresh_build.report.move_bucket_counts,
            refresh_changed_field_counts=(
                refresh_build.report.changed_field_counts
            ),
            refresh_large_move_sample=tuple(
                {
                    "cafe_id": refresh.cafe_id,
                    "kakao_place_id": refresh.kakao_place_id,
                    "name": refresh.name,
                    "movement_m": round(refresh.movement_m, 3),
                    "quarantined": (
                        refresh.cafe_id in quarantined_large_move_ids
                    ),
                    "quarantine_reason": (
                        KAKAO_LARGE_MOVE_QUARANTINE_REASON
                        if refresh.cafe_id in quarantined_large_move_ids
                        else None
                    ),
                }
                for refresh in sorted(
                    refresh_build.refreshes,
                    key=lambda item: (-item.movement_m, item.cafe_id),
                )
                if refresh.movement_m > KAKAO_CATALOG_REFRESH_LARGE_MOVE_M
            )[:20],
            planned_cafe_insert_count=len(candidates),
            planned_provider_insert_count=len(candidates),
            inserted_cafe_count=inserted_cafes,
            inserted_provider_count=inserted_providers,
            candidate_ids_sha256=build.report.candidate_ids_sha256,
            max_expected_candidates=max_expected_candidates,
            max_expected_large_moves=max_expected_large_moves,
        )
    except Exception:
        session.rollback()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kakao-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--kakao-manifest", type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--max-large-moves", type=int)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = args.kakao_manifest or manifest_path_for(args.kakao_cache)
    try:
        snapshot = read_validated_kakao_snapshot(args.kakao_cache, manifest)
        engine = create_db_engine(args.database_url)
        try:
            with Session(engine) as session:
                report = seed_kakao_catalog_expansion(
                    session,
                    snapshot,
                    apply=args.apply,
                    max_expected_candidates=args.max_candidates,
                    max_expected_large_moves=args.max_large_moves,
                )
        finally:
            engine.dispose()
    except Exception as exc:
        print(
            f"Kakao catalog seed failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            asdict(report),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
