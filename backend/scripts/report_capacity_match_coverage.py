#!/usr/bin/env python3
"""Read-only aggregate coverage report for permit venue-area matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import (
    PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
    SEOUL_REFRESHMENT_PERMIT_AREA_TARGET_BUSINESS_TYPE,
    SEOUL_REFRESHMENT_PERMIT_AREA_UNIT,
    SEOUL_REFRESHMENT_PERMIT_AREA_UNIT_STATUS,
    SEOUL_REFRESHMENT_PERMIT_DATASET_ID,
    SEOUL_REFRESHMENT_PERMIT_SERVICE,
)
from app.database import create_db_engine
from app.geo import EARTH_RADIUS_M, haversine_m
from app.ingest.permit_cafe_entity_resolution import (
    CafeEntityRecord,
    PermitEntityRecord,
    normalize_phone_digits,
    resolve_permit_to_cafes,
)
from app.ingest.seoul_refreshment_candidates import PlaceCandidate
from app.models import Cafe
from scripts.cache_refreshment_candidates import (
    CandidateCacheError,
    read_candidate_cache,
)


REPORT_VERSION = "v1-production-capacity-match-coverage"
INPUT_CONTRACT_VERSION = "oa-16095-place-candidate-area-v1"
TARGET_CATEGORY = SEOUL_REFRESHMENT_PERMIT_AREA_TARGET_BUSINESS_TYPE
AREA_UNIT = SEOUL_REFRESHMENT_PERMIT_AREA_UNIT
AREA_UNIT_STATUS = SEOUL_REFRESHMENT_PERMIT_AREA_UNIT_STATUS
PERCENTILES = (1, 5, 50, 95, 99)


class CapacityCoverageError(RuntimeError):
    """Raised when report inputs cannot be audited without guessing."""


class ReadOnlySession(Protocol):
    def get_bind(self): ...

    def execute(self, statement): ...


@dataclass(frozen=True, slots=True)
class CapacityCafe:
    cafe_id: str
    name: str | None
    latitude: float
    longitude: float
    road_address: str | None
    phone: str | None
    origin_provider: str


def enforce_transaction_read_only(session: ReadOnlySession) -> None:
    """Make PostgreSQL reject every write in this report transaction."""

    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_versioned_candidate_cache(
    cache_path: Path,
    manifest_path: Path,
) -> tuple[tuple[PlaceCandidate, ...], dict[str, Any]]:
    """Validate immutable cache provenance before exposing parsed candidates."""

    try:
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapacityCoverageError("candidate manifest is unreadable") from exc
    if not isinstance(manifest_value, dict):
        raise CapacityCoverageError("candidate manifest must be an object")
    manifest = manifest_value
    if manifest.get("dataset_id") != SEOUL_REFRESHMENT_PERMIT_DATASET_ID:
        raise CapacityCoverageError("candidate manifest dataset mismatch")
    if manifest.get("service") != SEOUL_REFRESHMENT_PERMIT_SERVICE:
        raise CapacityCoverageError("candidate manifest service mismatch")
    actual_sha = _sha256(cache_path)
    if manifest.get("cache_sha256") != actual_sha:
        raise CapacityCoverageError("candidate cache SHA-256 mismatch")
    try:
        candidates = read_candidate_cache(cache_path)
    except (OSError, CandidateCacheError) as exc:
        raise CapacityCoverageError("candidate cache is unreadable") from exc
    candidate_count = manifest.get("candidate_count")
    if isinstance(candidate_count, bool) or candidate_count != len(candidates):
        raise CapacityCoverageError("candidate cache count mismatch")
    return candidates, {
        "contract_version": INPUT_CONTRACT_VERSION,
        "dataset_id": SEOUL_REFRESHMENT_PERMIT_DATASET_ID,
        "service": SEOUL_REFRESHMENT_PERMIT_SERVICE,
        "cache_sha256": actual_sha,
        "candidate_count": len(candidates),
    }


def load_active_cafes(session: Session) -> tuple[CapacityCafe, ...]:
    """Read only matcher-required columns from active production cafes."""

    enforce_transaction_read_only(session)
    rows = session.execute(
        select(
            Cafe.id,
            Cafe.name,
            Cafe.lat,
            Cafe.lng,
            Cafe.road_address,
            Cafe.phone,
            Cafe.origin_provider,
        )
        .where(Cafe.active.is_(True))
        .order_by(Cafe.id)
    ).all()
    return tuple(
        CapacityCafe(
            cafe_id=str(row.id),
            name=str(row.name) if row.name is not None else None,
            latitude=float(row.lat),
            longitude=float(row.lng),
            road_address=(
                str(row.road_address) if row.road_address is not None else None
            ),
            phone=str(row.phone) if row.phone is not None else None,
            origin_provider=str(row.origin_provider),
        )
        for row in rows
    )


def _usable_phone(value: str | None) -> str | None:
    try:
        return normalize_phone_digits(value)
    except ValueError:
        return None


def _grid_cell(
    latitude: float,
    longitude: float,
    *,
    cell_size_m: float,
) -> tuple[int, int, int]:
    """Hash WGS84 point into an ECEF cube with a physical metre edge."""

    haversine_m(latitude, longitude, latitude, longitude)
    lat = math.radians(latitude)
    lng = math.radians(longitude)
    cos_lat = math.cos(lat)
    return (
        math.floor(EARTH_RADIUS_M * cos_lat * math.cos(lng) / cell_size_m),
        math.floor(EARTH_RADIUS_M * cos_lat * math.sin(lng) / cell_size_m),
        math.floor(EARTH_RADIUS_M * math.sin(lat) / cell_size_m),
    )


def _neighbor_cells(cell: tuple[int, int, int]):
    x, y, z = cell
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                yield x + dx, y + dy, z + dz


def _eligible_area(candidate: PlaceCandidate) -> Decimal | None:
    if (
        candidate.category != TARGET_CATEGORY
        or candidate.facility_area_status != "eligible"
        or candidate.facility_area_unit != AREA_UNIT
        or candidate.facility_area_unit_status != AREA_UNIT_STATUS
        or candidate.facility_area_m2 is None
    ):
        return None
    try:
        area = Decimal(candidate.facility_area_m2)
    except InvalidOperation as exc:
        raise CapacityCoverageError("eligible facility area is not decimal") from exc
    if not area.is_finite() or area <= 0:
        raise CapacityCoverageError("eligible facility area is not positive")
    return area


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _nearest_rank(values: Sequence[Decimal], percentile: int) -> Decimal:
    if not values:
        raise ValueError("nearest-rank percentile requires values")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in 1..100")
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


def build_capacity_coverage(
    candidates: Sequence[PlaceCandidate],
    cafes: Sequence[CapacityCafe],
) -> dict[str, Any]:
    """Resolve eligible permits and return aggregate-only deterministic facts."""

    sorted_cafes = sorted(cafes, key=lambda item: item.cafe_id)
    if len({item.cafe_id for item in sorted_cafes}) != len(sorted_cafes):
        raise CapacityCoverageError("active cafe IDs are not unique")
    grid: dict[tuple[int, int, int], list[CapacityCafe]] = defaultdict(list)
    provider_by_id: dict[str, str] = {}
    for cafe in sorted_cafes:
        if not cafe.origin_provider:
            raise CapacityCoverageError("active cafe origin provider is empty")
        grid[
            _grid_cell(
                cafe.latitude,
                cafe.longitude,
                cell_size_m=PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
            )
        ].append(cafe)
        provider_by_id[cafe.cafe_id] = cafe.origin_provider

    eligible: list[tuple[PlaceCandidate, Decimal]] = []
    for candidate in candidates:
        area = _eligible_area(candidate)
        if area is not None:
            eligible.append((candidate, area))
    eligible.sort(key=lambda item: item[0].source_id)
    if len({item[0].source_id for item in eligible}) != len(eligible):
        raise CapacityCoverageError("eligible permit IDs are not unique")

    statuses: Counter[str] = Counter()
    evidence_rules: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    matched_areas: list[Decimal] = []
    nearby_candidate_pair_count = 0
    max_nearby_cafes_per_permit = 0
    for candidate, area in eligible:
        cell = _grid_cell(
            candidate.latitude,
            candidate.longitude,
            cell_size_m=PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
        )
        nearby = sorted(
            (
                cafe
                for neighbor in _neighbor_cells(cell)
                for cafe in grid.get(neighbor, ())
            ),
            key=lambda item: item.cafe_id,
        )
        nearby_candidate_pair_count += len(nearby)
        max_nearby_cafes_per_permit = max(
            max_nearby_cafes_per_permit, len(nearby)
        )
        resolution = resolve_permit_to_cafes(
            PermitEntityRecord(
                permit_id=candidate.source_id,
                name=candidate.name,
                address=candidate.road_address,
                phone=_usable_phone(candidate.phone),
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                coordinate_unit="wgs84_degrees",
                coordinate_unit_verified=True,
            ),
            tuple(
                CafeEntityRecord(
                    cafe_id=cafe.cafe_id,
                    name=cafe.name,
                    address=cafe.road_address,
                    phone=_usable_phone(cafe.phone),
                    latitude=cafe.latitude,
                    longitude=cafe.longitude,
                    coordinate_unit="wgs84_degrees",
                    coordinate_unit_verified=True,
                )
                for cafe in nearby
            ),
        )
        statuses[resolution.status] += 1
        if resolution.status != "verified":
            continue
        evidence = resolution.strong_candidates[0]
        rule = (
            "both"
            if evidence.normalized_name_exact and evidence.normalized_phone_exact
            else "name_only"
            if evidence.normalized_name_exact
            else "phone_only"
        )
        evidence_rules[rule] += 1
        matched_areas.append(area)
        if resolution.verified_cafe_id is None:
            raise CapacityCoverageError("verified resolution has no cafe")
        provider_counts[provider_by_id[resolution.verified_cafe_id]] += 1

    area_distribution: dict[str, Any] = {
        "samples": len(matched_areas),
        "unit": AREA_UNIT,
        "percentile_method": "nearest_rank",
        "min": _decimal_text(min(matched_areas)) if matched_areas else None,
        **{
            f"p{percentile}": (
                _decimal_text(_nearest_rank(matched_areas, percentile))
                if matched_areas
                else None
            )
            for percentile in PERCENTILES
        },
        "max": _decimal_text(max(matched_areas)) if matched_areas else None,
    }
    return {
        "report_version": REPORT_VERSION,
        "scope": {
            "input_candidate_count": len(candidates),
            "eligible_coffee_permit_count": len(eligible),
            "active_cafe_count": len(sorted_cafes),
            "max_distance_m": PERMIT_CAFE_ENTITY_MAX_DISTANCE_M,
            "required_category": TARGET_CATEGORY,
            "required_area_status": "eligible",
            "required_area_unit": AREA_UNIT,
            "required_area_unit_status": AREA_UNIT_STATUS,
            "nearby_candidate_pair_count": nearby_candidate_pair_count,
            "max_nearby_cafes_per_permit": max_nearby_cafes_per_permit,
        },
        "resolution_counts": {
            status: statuses[status]
            for status in ("verified", "missing", "ambiguous")
        },
        "verified_evidence_rule_counts": {
            rule: evidence_rules[rule]
            for rule in ("name_only", "phone_only", "both")
        },
        "matched_area_m2": area_distribution,
        "matched_cafe_origin_provider_counts": dict(sorted(provider_counts.items())),
    }


def serialize_report(report: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def publish_report(path: Path, serialized: bytes) -> None:
    """Atomically create one aggregate report and refuse every overwrite."""

    part = path.with_name(path.name + ".part")
    path.parent.mkdir(parents=True, exist_ok=True)
    for candidate in (path, part):
        if candidate.exists():
            raise FileExistsError(f"refusing to overwrite output: {candidate}")
    descriptor = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(part, path)
    finally:
        part.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    manifest_path = args.manifest or args.cache.with_suffix(".manifest.json")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("capacity coverage report failed: DATABASE_URL is required", file=sys.stderr)
        return 1
    engine = create_db_engine(database_url)
    try:
        candidates, provenance = load_versioned_candidate_cache(
            args.cache, manifest_path
        )
        with Session(engine) as session, session.begin():
            cafes = load_active_cafes(session)
            report = build_capacity_coverage(candidates, cafes)
        report["input"] = provenance
        serialized = serialize_report(report)
        if args.output is not None:
            publish_report(args.output, serialized)
        sys.stdout.buffer.write(serialized)
    except (OSError, SQLAlchemyError, ValueError, CapacityCoverageError) as exc:
        print(
            f"capacity coverage report failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
