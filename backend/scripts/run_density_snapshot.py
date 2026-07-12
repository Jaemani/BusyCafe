"""Summarize the offline v3 density model over the latest cached snapshots.

This read-only report has no ground truth and is NOT accuracy evidence. It only
describes the structural distribution of the deterministic density challenger
(coverage, selection mode, and population-density spread) and never writes to
the database.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median

from shapely.geometry import Point
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    DENSITY_SHADOW_MODEL_VERSION,
    SEOUL_HOTSPOT_AREAS_PATH,
    SEOUL_HOTSPOT_LIST_PATH,
)
from app.database import create_db_engine
from app.ingest.hotspot_master import (
    HotspotGeometryRecord,
    load_hotspot_geometry_master,
)
from app.models import Cafe, Hotspot, HotspotSnapshot
from app.scoring.density_shadow import (
    DensityHotspotObservation,
    score_cafe_density_shadow,
)
from app.scoring.polygon_shadow import PolygonHotspotGeometry
from scripts.run_shadow_eval import bind_hotspot_geometries


Coverage = str
SelectionMode = str
GeometryLoader = Callable[
    [str | Path, str | Path], tuple[HotspotGeometryRecord, ...]
]
_COVERAGE_ORDER = ("covered", "fringe", "uncovered")
_SELECTION_ORDER = ("inside", "boundary", "uncovered")


@dataclass(frozen=True, slots=True)
class DensityStructuralReport:
    model_version: str
    geometry_versions: tuple[str, ...]
    as_of: datetime
    active_cafes: int
    hotspots_total: int
    hotspots_missing_ppltn: int
    coverage_counts: tuple[tuple[Coverage, int], ...]
    selection_counts: tuple[tuple[SelectionMode, int], ...]
    scored_cafes: int
    overlap_contributor_cafes: int
    min_density_per_m2: float | None
    mean_density_per_m2: float | None
    median_density_per_m2: float | None
    max_density_per_m2: float | None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def build_density_observations(
    session: Session,
    geometries: dict[int, PolygonHotspotGeometry],
) -> tuple[DensityHotspotObservation, ...]:
    """Join the latest cached population snapshot to each verified geometry."""

    latest = (
        select(
            HotspotSnapshot.hotspot_id,
            func.max(HotspotSnapshot.observed_at).label("observed_at"),
        )
        .group_by(HotspotSnapshot.hotspot_id)
        .subquery()
    )
    rows = session.execute(
        select(
            Hotspot.id,
            HotspotSnapshot.ppltn_min,
            HotspotSnapshot.ppltn_max,
            HotspotSnapshot.observed_at,
        )
        .join(latest, latest.c.hotspot_id == Hotspot.id)
        .join(
            HotspotSnapshot,
            (HotspotSnapshot.hotspot_id == latest.c.hotspot_id)
            & (HotspotSnapshot.observed_at == latest.c.observed_at),
        )
        .where(Hotspot.is_polled.is_(True))
        .order_by(Hotspot.id)
    ).all()
    if set(geometries) != {row.id for row in rows}:
        raise ValueError("latest snapshot and geometry hotspot sets differ")
    return tuple(
        DensityHotspotObservation(
            hotspot_id=row.id,
            area_cd=geometries[row.id].area_cd,
            name=geometries[row.id].name,
            geometry_version=geometries[row.id].geometry_version,
            geometry_normalization=geometries[row.id].geometry_normalization,
            geometry=geometries[row.id].geometry,
            ppltn_min=row.ppltn_min,
            ppltn_max=row.ppltn_max,
            observed_at=_as_utc(row.observed_at),
        )
        for row in rows
    )


def build_density_structural_report(
    session: Session,
    geometries: dict[int, PolygonHotspotGeometry],
) -> DensityStructuralReport:
    """Score every active cafe with the density model; never persist output."""

    observations = build_density_observations(session, geometries)
    if not observations:
        raise ValueError("no latest polled hotspot snapshots available")

    as_of = max(item.observed_at for item in observations)
    hotspots_missing_ppltn = sum(
        item.ppltn_min is None or item.ppltn_max is None for item in observations
    )
    cafes = session.execute(
        select(Cafe.id, Cafe.lat, Cafe.lng)
        .where(Cafe.active.is_(True))
        .order_by(Cafe.id)
    ).all()

    coverage_counts = {name: 0 for name in _COVERAGE_ORDER}
    selection_counts = {name: 0 for name in _SELECTION_ORDER}
    densities: list[float] = []
    overlaps = 0
    for _cafe_id, cafe_lat, cafe_lng in cafes:
        estimate = score_cafe_density_shadow(
            cafe_lat,
            cafe_lng,
            observations,
            now=as_of,
        )
        coverage_counts[estimate.coverage] += 1
        selection_counts[estimate.selection_mode] += 1
        if estimate.density_per_m2 is not None:
            densities.append(estimate.density_per_m2)
        cafe_point = Point(cafe_lng, cafe_lat)
        if (
            sum(
                geometry.geometry.covers(cafe_point)
                for geometry in geometries.values()
            )
            > 1
        ):
            overlaps += 1

    return DensityStructuralReport(
        model_version=DENSITY_SHADOW_MODEL_VERSION,
        geometry_versions=tuple(
            sorted({geometry.geometry_version for geometry in geometries.values()})
        ),
        as_of=as_of,
        active_cafes=len(cafes),
        hotspots_total=len(observations),
        hotspots_missing_ppltn=hotspots_missing_ppltn,
        coverage_counts=tuple(
            (name, coverage_counts[name]) for name in _COVERAGE_ORDER
        ),
        selection_counts=tuple(
            (name, selection_counts[name]) for name in _SELECTION_ORDER
        ),
        scored_cafes=len(densities),
        overlap_contributor_cafes=overlaps,
        min_density_per_m2=min(densities) if densities else None,
        mean_density_per_m2=mean(densities) if densities else None,
        median_density_per_m2=median(densities) if densities else None,
        max_density_per_m2=max(densities) if densities else None,
    )


def _metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.9f}"


def render_markdown(report: DensityStructuralReport) -> str:
    versions = ", ".join(f"`{item}`" for item in report.geometry_versions) or "N/A"
    lines = [
        "# Density Structural Snapshot",
        "",
        "> **NOT accuracy evidence.** This report has no ground truth. It only "
        "describes the structural distribution of one deterministic density "
        "model. There is no 1-4 level mapping until a calibrated baseline exists.",
        "",
        f"- Model: `{report.model_version}`",
        f"- Geometry version(s): {versions}",
        f"- Snapshot as-of: `{report.as_of.isoformat()}`",
        f"- Active cafes: {report.active_cafes}",
        f"- Polled hotspots: {report.hotspots_total}",
        f"- Hotspots excluded (missing ppltn): {report.hotspots_missing_ppltn}",
        f"- Overlap-contributor cafes: {report.overlap_contributor_cafes}",
        "- Database writes: none",
        "",
        "## Coverage distribution",
        "",
        "| Coverage | Cafes |",
        "| --- | ---: |",
    ]
    lines.extend(
        f"| {name} | {count} |" for name, count in report.coverage_counts
    )
    lines.extend(
        [
            "",
            "## Selection-mode distribution",
            "",
            "| Selection mode | Cafes |",
            "| --- | ---: |",
        ]
    )
    lines.extend(
        f"| {name} | {count} |" for name, count in report.selection_counts
    )
    lines.extend(
        [
            "",
            "## Population density (people/m^2)",
            "",
            "| Measure | Value |",
            "| --- | ---: |",
            f"| Scored cafes | {report.scored_cafes} |",
            f"| Min density | {_metric(report.min_density_per_m2)} |",
            f"| Mean density | {_metric(report.mean_density_per_m2)} |",
            f"| Median density | {_metric(report.median_density_per_m2)} |",
            f"| Max density | {_metric(report.max_density_per_m2)} |",
        ]
    )
    return "\n".join(lines) + "\n"


def main(
    argv: Sequence[str] | None = None,
    *,
    geometry_loader: GeometryLoader = load_hotspot_geometry_master,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument("--output", type=Path, help="write Markdown report")
    parser.add_argument(
        "--hotspot-list",
        type=Path,
        default=SEOUL_HOTSPOT_LIST_PATH,
        help="verified OA-21285 hotspot XLSX",
    )
    parser.add_argument(
        "--hotspot-areas",
        type=Path,
        default=SEOUL_HOTSPOT_AREAS_PATH,
        help="verified OA-21285 WGS84 polygon ZIP",
    )
    args = parser.parse_args(argv)

    try:
        records = geometry_loader(args.hotspot_list, args.hotspot_areas)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))

    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            try:
                geometries = bind_hotspot_geometries(session, records)
                report = build_density_structural_report(session, geometries)
            except ValueError as exc:
                parser.error(str(exc))
    finally:
        engine.dispose()

    markdown = render_markdown(report)
    if args.output is None:
        print(markdown, end="")
    else:
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
