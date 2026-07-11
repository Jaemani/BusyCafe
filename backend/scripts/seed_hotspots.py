"""Seed the verified official Seoul hotspot master into the application DB."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    MAX_POLLED_HOTSPOTS,
    SEOUL_HOTSPOT_AREAS_PATH,
    SEOUL_HOTSPOT_LIST_PATH,
    TARGET_NEIGHBORHOODS,
    Neighborhood,
)
from app.database import create_db_engine
from app.geo import haversine_m
from app.ingest.hotspot_master import HotspotMasterRecord, load_hotspot_master
from app.models import Hotspot


class HotspotSeedError(RuntimeError):
    """Raised when safe hotspot seeding invariants are not met."""


@dataclass(frozen=True, slots=True)
class NeighborhoodDistance:
    key: str
    distance_m: float


@dataclass(frozen=True, slots=True)
class ManualReviewRow:
    area_cd: str
    name: str
    category: str
    lat: float
    lng: float
    neighborhoods: tuple[NeighborhoodDistance, ...]


@dataclass(frozen=True, slots=True)
class SeedReport:
    source_count: int
    inserted_count: int
    updated_count: int
    unchanged_count: int
    polled_count: int
    max_polled_hotspots: int
    dry_run: bool
    manual_review: tuple[ManualReviewRow, ...]

    @property
    def polled_area_codes(self) -> tuple[str, ...]:
        return tuple(row.area_cd for row in self.manual_review)


def _neighborhood_distances(
    record: HotspotMasterRecord,
    neighborhoods: Mapping[str, Neighborhood],
) -> tuple[NeighborhoodDistance, ...]:
    matches = [
        NeighborhoodDistance(
            key=key,
            distance_m=haversine_m(
                record.lat, record.lng, neighborhood.lat, neighborhood.lng
            ),
        )
        for key, neighborhood in neighborhoods.items()
    ]
    return tuple(sorted(matches, key=lambda item: item.key))


def select_polled_hotspots(
    records: Sequence[HotspotMasterRecord],
    *,
    neighborhoods: Mapping[str, Neighborhood] = TARGET_NEIGHBORHOODS,
    max_polled_hotspots: int = MAX_POLLED_HOTSPOTS,
) -> tuple[ManualReviewRow, ...]:
    """Select the union of hotspot points inside any target neighborhood."""

    if max_polled_hotspots < 1:
        raise ValueError("max_polled_hotspots must be positive")
    if not neighborhoods:
        raise ValueError("at least one target neighborhood is required")

    selected: list[ManualReviewRow] = []
    for record in sorted(records, key=lambda item: item.area_cd):
        distances = _neighborhood_distances(record, neighborhoods)
        matches = tuple(
            item
            for item in distances
            if item.distance_m <= neighborhoods[item.key].radius_m
        )
        if matches:
            selected.append(
                ManualReviewRow(
                    area_cd=record.area_cd,
                    name=record.name,
                    category=record.category,
                    lat=record.lat,
                    lng=record.lng,
                    neighborhoods=matches,
                )
            )

    if len(selected) > max_polled_hotspots:
        codes = ", ".join(row.area_cd for row in selected)
        raise HotspotSeedError(
            f"selected {len(selected)} hotspots, exceeding "
            f"MAX_POLLED_HOTSPOTS={max_polled_hotspots}: {codes}"
        )
    return tuple(selected)


def seed_hotspots(
    session: Session,
    records: Sequence[HotspotMasterRecord],
    *,
    neighborhoods: Mapping[str, Neighborhood] = TARGET_NEIGHBORHOODS,
    max_polled_hotspots: int = MAX_POLLED_HOTSPOTS,
    dry_run: bool = False,
) -> SeedReport:
    """Idempotently insert/update all master records and return an audit report."""

    records_by_code = {record.area_cd: record for record in records}
    if len(records_by_code) != len(records):
        raise HotspotSeedError("records contain duplicate AREA_CD values")

    existing_by_code = {
        hotspot.area_cd: hotspot for hotspot in session.scalars(select(Hotspot))
    }
    extra_codes = sorted(existing_by_code.keys() - records_by_code.keys())
    if extra_codes:
        # This command owns its transaction boundary. Roll back any autoflush
        # triggered by the complete-set query and fail closed: deleting an
        # unknown row could also cascade real snapshots.
        session.rollback()
        raise HotspotSeedError(
            "database contains AREA_CD values absent from the official master: "
            + ", ".join(extra_codes)
        )

    review_rows = select_polled_hotspots(
        records,
        neighborhoods=neighborhoods,
        max_polled_hotspots=max_polled_hotspots,
    )
    polled_codes = {row.area_cd for row in review_rows}

    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    for area_cd in sorted(records_by_code):
        record = records_by_code[area_cd]
        values = {
            "name": record.name,
            "category": record.category,
            "lat": record.lat,
            "lng": record.lng,
            "is_polled": area_cd in polled_codes,
        }
        existing = existing_by_code.get(area_cd)
        if existing is None:
            inserted_count += 1
            if not dry_run:
                session.add(Hotspot(area_cd=area_cd, **values))
            continue

        changed = any(getattr(existing, key) != value for key, value in values.items())
        if not changed:
            unchanged_count += 1
            continue
        updated_count += 1
        if not dry_run:
            for key, value in values.items():
                setattr(existing, key, value)

    if not dry_run:
        session.commit()

    return SeedReport(
        source_count=len(records),
        inserted_count=inserted_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        polled_count=len(review_rows),
        max_polled_hotspots=max_polled_hotspots,
        dry_run=dry_run,
        manual_review=review_rows,
    )


def format_report(report: SeedReport) -> str:
    """Render a stable human-readable seed and manual-review report."""

    action = "would insert/update" if report.dry_run else "inserted/updated"
    lines = [
        "Hotspot seed report",
        f"mode: {'dry-run' if report.dry_run else 'write'}",
        f"source records: {report.source_count}",
        (
            f"{action}: {report.inserted_count}/{report.updated_count} "
            f"(unchanged: {report.unchanged_count})"
        ),
        (
            f"polling targets: {report.polled_count}/"
            f"{report.max_polled_hotspots}"
        ),
        "manual review required:",
    ]
    lines.extend(
        (
            f"- {row.area_cd} | {row.name} | {row.category} | "
            f"{row.lat:.6f},{row.lng:.6f} | "
            + ", ".join(
                f"{item.key}={item.distance_m:.0f}m"
                for item in row.neighborhoods
            )
        )
        for row in report.manual_review
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument(
        "--xlsx",
        type=Path,
        default=SEOUL_HOTSPOT_LIST_PATH,
        help="official Seoul hotspot XLSX path",
    )
    parser.add_argument(
        "--areas-zip",
        type=Path,
        default=SEOUL_HOTSPOT_AREAS_PATH,
        help="official Seoul hotspot Shapefile ZIP path",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write verified records after HUMAN review (default: dry-run)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    records = load_hotspot_master(args.xlsx, args.areas_zip)
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = seed_hotspots(session, records, dry_run=not args.apply)
        print(format_report(report))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
