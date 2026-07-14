#!/usr/bin/env python3
"""Dry-run provider cafe/link seed; writes require explicit ``--apply``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import (
    PROVIDER_CAFE_RELEASE,
    PROVIDER_LAST_SEEN_UPDATE_BATCH_SIZE,
    PROVIDER_VERIFIED_CAFE_CONFIDENCE,
)
from app.database import create_db_engine
from app.ingest.overture_places import overture_seed_value_equal
from app.ingest.provider_cafe_catalog import (
    PERMIT_DATASET_ID,
    PERMIT_PROVENANCE,
    ProviderCatalogError,
    ProviderCatalogRecords,
    ProviderNeutralCafeCandidate,
    ProviderReference,
    read_complete_provider_catalog,
)
from app.models import Cafe, CafeProviderPlace
from scripts.build_provider_cafe_catalog import DEFAULT_OUTPUT


class ProviderSeedError(RuntimeError):
    """Raised before writes when provider ownership is inconsistent."""


_MANAGED_KAKAO_MATCH_METHODS = frozenset(
    {
        "exact_name",
        "exact_phone",
        "exact_name_and_phone",
        "exact_name_and_address",
        "exact_phone_and_address",
        "exact_name_and_phone_and_address",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderSeedReport:
    cafe_source_count: int
    provider_reference_count: int
    cafe_inserted_count: int
    cafe_updated_count: int
    cafe_unchanged_count: int
    cafe_deactivated_count: int
    provider_inserted_count: int
    provider_updated_count: int
    provider_unchanged_count: int
    provider_deactivated_count: int
    cafe_changed_field_counts: tuple[tuple[str, int], ...]
    dry_run: bool


@dataclass(frozen=True, slots=True)
class ProviderSeedStage:
    preflight: ProviderSeedReport
    applied: ProviderSeedReport | None


def _source_provenance(
    candidate: ProviderNeutralCafeCandidate,
) -> list[dict[str, object]]:
    return [
        {
            "dataset_id": PERMIT_DATASET_ID,
            "management_number": candidate.canonical_source_id,
            "provenance": PERMIT_PROVENANCE,
            "provider_confirmation": [
                {
                    "provider": reference.provider,
                    "provider_place_id": reference.provider_place_id,
                    "match_rule": reference.match_rule,
                    "distance_m": reference.match_distance_m,
                }
                for reference in sorted(
                    candidate.provider_refs,
                    key=lambda item: (item.provider, item.provider_place_id),
                )
            ],
        }
    ]


def _cafe_values(
    candidate: ProviderNeutralCafeCandidate,
) -> dict[str, object]:
    return {
        "source_release": PROVIDER_CAFE_RELEASE,
        "source_confidence": PROVIDER_VERIFIED_CAFE_CONFIDENCE,
        "primary_category": "cafe",
        "name": candidate.name,
        "lat": candidate.latitude,
        "lng": candidate.longitude,
        "road_address": candidate.road_address or candidate.lot_address,
        "phone": candidate.phone,
        "website": None,
        "source_json": _source_provenance(candidate),
        "external_links_json": None,
        "active": True,
    }


def _all_references(catalog: ProviderCatalogRecords) -> tuple[ProviderReference, ...]:
    references = [*catalog.existing_provider_refs]
    for candidate in catalog.new_cafe_candidates:
        references.extend(candidate.provider_refs)
    return tuple(
        sorted(
            references,
            key=lambda item: (
                item.canonical_source,
                item.canonical_source_id,
                item.provider,
                item.provider_place_id,
            ),
        )
    )


def seed_provider_cafes(
    session: Session,
    catalog: ProviderCatalogRecords,
    *,
    dry_run: bool,
    now: datetime | None = None,
) -> ProviderSeedReport:
    """Upsert provider catalog and retire only links owned by this pipeline."""

    seen_at = now or datetime.now(UTC)
    if seen_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    seen_at = seen_at.astimezone(UTC)

    cafes = tuple(session.scalars(select(Cafe)))
    cafes_by_origin = {
        (cafe.origin_provider, cafe.origin_source_id): cafe for cafe in cafes
    }
    cafes_by_id = {cafe.id: cafe for cafe in cafes}
    if len(cafes_by_origin) != len(cafes):
        raise ProviderSeedError("database contains duplicate cafe origins")

    provider_places = tuple(session.scalars(select(CafeProviderPlace)))
    providers_by_key = {
        (place.provider, place.provider_place_id): place
        for place in provider_places
    }
    providers_by_cafe_provider = {
        (place.cafe_id, place.provider): place for place in provider_places
    }
    if len(providers_by_key) != len(provider_places):
        raise ProviderSeedError("database contains duplicate provider place IDs")
    if len(providers_by_cafe_provider) != len(provider_places):
        raise ProviderSeedError("database contains duplicate cafe providers")

    candidates_by_origin = {
        (candidate.canonical_source, candidate.canonical_source_id): candidate
        for candidate in catalog.new_cafe_candidates
    }
    if len(candidates_by_origin) != len(catalog.new_cafe_candidates):
        raise ProviderCatalogError("provider catalog contains duplicate cafe origins")

    references = _all_references(catalog)
    incoming_provider_owners: dict[
        tuple[str, str], tuple[str, str]
    ] = {}
    incoming_cafe_providers: dict[
        tuple[str, str, str], str
    ] = {}
    incoming_reference_keys: set[tuple[str, str, str, str]] = set()
    for reference in references:
        origin = (reference.canonical_source, reference.canonical_source_id)
        provider_key = (reference.provider, reference.provider_place_id)
        reference_key = (*origin, *provider_key)
        if reference_key in incoming_reference_keys:
            raise ProviderSeedError("incoming provider reference is duplicated")
        incoming_reference_keys.add(reference_key)
        previous_owner = incoming_provider_owners.setdefault(provider_key, origin)
        if previous_owner != origin:
            raise ProviderSeedError(
                "incoming provider place ID has multiple canonical owners"
            )
        cafe_provider_key = (*origin, reference.provider)
        previous_place_id = incoming_cafe_providers.setdefault(
            cafe_provider_key, reference.provider_place_id
        )
        if previous_place_id != reference.provider_place_id:
            raise ProviderSeedError(
                "incoming canonical cafe has multiple IDs for one provider"
            )
    # Validate every target and every DB uniqueness constraint before mutation.
    for reference in references:
        origin = (reference.canonical_source, reference.canonical_source_id)
        target = cafes_by_origin.get(origin)
        if target is None and origin not in candidates_by_origin:
            raise ProviderSeedError(
                "provider reference targets a missing canonical cafe"
            )
        owner = providers_by_key.get(
            (reference.provider, reference.provider_place_id)
        )
        if owner is not None:
            owner_cafe = cafes_by_id.get(owner.cafe_id)
            if owner_cafe is None or (
                owner_cafe.origin_provider,
                owner_cafe.origin_source_id,
            ) != origin:
                raise ProviderSeedError(
                    "provider place ID belongs to another canonical cafe"
                )
        if target is not None:
            current = providers_by_cafe_provider.get(
                (target.id, reference.provider)
            )
            if (
                current is not None
                and current.provider_place_id != reference.provider_place_id
            ):
                raise ProviderSeedError(
                    "canonical cafe already has another provider place ID"
                )

    cafe_inserted = cafe_updated = cafe_unchanged = 0
    changed_field_counts: dict[str, int] = {}
    created_by_origin: dict[tuple[str, str], Cafe] = {}
    for origin in sorted(candidates_by_origin):
        candidate = candidates_by_origin[origin]
        values = _cafe_values(candidate)
        existing = cafes_by_origin.get(origin)
        if existing is None:
            cafe_inserted += 1
            if not dry_run:
                created = Cafe(
                    origin_provider=candidate.canonical_source,
                    origin_source_id=candidate.canonical_source_id,
                    overture_id=None,
                    **values,
                )
                created_by_origin[origin] = created
            continue
        changed_fields = tuple(
            field
            for field, value in values.items()
            if not overture_seed_value_equal(
                field, getattr(existing, field), value
            )
        )
        if not changed_fields:
            cafe_unchanged += 1
            continue
        cafe_updated += 1
        for field in changed_fields:
            changed_field_counts[field] = changed_field_counts.get(field, 0) + 1
            if not dry_run:
                setattr(existing, field, values[field])

    if created_by_origin:
        session.add_all(created_by_origin.values())
        # Provider references need canonical cafe IDs. Flush the full cafe
        # batch once instead of forcing one remote round trip per cafe.
        session.flush()

    provider_inserted = provider_updated = provider_unchanged = 0
    unchanged_provider_ids: list[int] = []
    for reference in references:
        origin = (reference.canonical_source, reference.canonical_source_id)
        target = cafes_by_origin.get(origin) or created_by_origin.get(origin)
        if target is None and not dry_run:
            raise ProviderSeedError("provider reference target disappeared")
        existing = providers_by_key.get(
            (reference.provider, reference.provider_place_id)
        )
        if existing is None:
            provider_inserted += 1
            if not dry_run:
                session.add(
                    CafeProviderPlace(
                        cafe_id=target.id,  # type: ignore[union-attr]
                        provider=reference.provider,
                        provider_place_id=reference.provider_place_id,
                        detail_url=reference.direct_url,
                        active=True,
                        match_method=reference.match_rule,
                        match_distance_m=reference.match_distance_m,
                        verified_at=seen_at,
                        last_seen_at=seen_at,
                    )
                )
            continue
        changed = (
            existing.detail_url != reference.direct_url
            or not existing.active
            or existing.match_method != reference.match_rule
            or existing.match_distance_m != reference.match_distance_m
        )
        if changed:
            provider_updated += 1
        else:
            provider_unchanged += 1
        if not dry_run and changed:
            existing.detail_url = reference.direct_url
            existing.active = True
            existing.match_method = reference.match_rule
            existing.match_distance_m = reference.match_distance_m
            existing.verified_at = seen_at
            existing.last_seen_at = seen_at
        elif not dry_run:
            unchanged_provider_ids.append(existing.id)

    incoming_provider_keys = {
        (reference.provider, reference.provider_place_id)
        for reference in references
    }
    provider_deactivated = 0
    for existing in provider_places:
        provider_key = (existing.provider, existing.provider_place_id)
        if (
            existing.active
            and existing.provider == "kakao"
            and existing.match_method in _MANAGED_KAKAO_MATCH_METHODS
            and provider_key not in incoming_provider_keys
        ):
            provider_deactivated += 1
            if not dry_run:
                existing.active = False

    if not dry_run:
        for offset in range(
            0,
            len(unchanged_provider_ids),
            PROVIDER_LAST_SEEN_UPDATE_BATCH_SIZE,
        ):
            batch = unchanged_provider_ids[
                offset : offset + PROVIDER_LAST_SEEN_UPDATE_BATCH_SIZE
            ]
            session.execute(
                update(CafeProviderPlace)
                .where(CafeProviderPlace.id.in_(batch))
                .values(last_seen_at=seen_at),
                execution_options={"synchronize_session": False},
            )
        session.commit()
    return ProviderSeedReport(
        cafe_source_count=len(catalog.new_cafe_candidates),
        provider_reference_count=len(references),
        cafe_inserted_count=cafe_inserted,
        cafe_updated_count=cafe_updated,
        cafe_unchanged_count=cafe_unchanged,
        cafe_deactivated_count=0,
        provider_inserted_count=provider_inserted,
        provider_updated_count=provider_updated,
        provider_unchanged_count=provider_unchanged,
        provider_deactivated_count=provider_deactivated,
        cafe_changed_field_counts=tuple(sorted(changed_field_counts.items())),
        dry_run=dry_run,
    )


def stage_provider_seed(
    session: Session,
    catalog: ProviderCatalogRecords,
    *,
    apply: bool,
) -> ProviderSeedStage:
    preflight = seed_provider_cafes(session, catalog, dry_run=True)
    if not apply:
        return ProviderSeedStage(preflight=preflight, applied=None)
    applied = seed_provider_cafes(session, catalog, dry_run=False)
    return ProviderSeedStage(preflight=preflight, applied=applied)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = args.manifest or args.cache.with_suffix(".manifest.json")
        catalog = read_complete_provider_catalog(args.cache, manifest)
        engine = create_db_engine(args.database_url)
        try:
            with Session(engine) as session:
                stage = stage_provider_seed(session, catalog, apply=args.apply)
        finally:
            engine.dispose()
    except Exception as exc:
        print(f"seed failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    report = stage.applied or stage.preflight
    print(f"mode: {'write' if stage.applied is not None else 'dry-run'}")
    print(
        "cafes inserted/updated/unchanged/deactivated: "
        f"{report.cafe_inserted_count}/{report.cafe_updated_count}/"
        f"{report.cafe_unchanged_count}/{report.cafe_deactivated_count}"
    )
    print(
        "provider links inserted/updated/unchanged/deactivated: "
        f"{report.provider_inserted_count}/{report.provider_updated_count}/"
        f"{report.provider_unchanged_count}/"
        f"{report.provider_deactivated_count}"
    )
    if report.cafe_changed_field_counts:
        fields = ", ".join(
            f"{field}={count}"
            for field, count in report.cafe_changed_field_counts
        )
        print(f"updated cafe fields: {fields}")
    else:
        print("updated cafe fields: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
