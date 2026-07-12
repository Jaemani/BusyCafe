"""Compare v1 point and v2 polygon structure on the latest cached snapshots.

This read-only report has no ground truth and is NOT accuracy evidence.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from shapely.geometry import Point
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    POLYGON_SHADOW_MODEL_VERSION,
    SCORING_MODEL_VERSION,
    SEOUL_HOTSPOT_AREAS_PATH,
    SEOUL_HOTSPOT_LIST_PATH,
    SHADOW_DIVERGENCE_AUDIT_LIMIT,
)
from app.database import create_db_engine
from app.ingest.hotspot_master import (
    HotspotGeometryRecord,
    load_hotspot_geometry_master,
)
from app.models import Cafe, Hotspot, HotspotSnapshot
from app.scoring.engine import HotspotObservation, score_cafe
from app.scoring.polygon_shadow import (
    PolygonHotspotGeometry,
    score_cafe_polygon_shadow_compatible,
)
from scripts.run_shadow_eval import bind_hotspot_geometries


Coverage = str
GeometryLoader = Callable[
    [str | Path, str | Path], tuple[HotspotGeometryRecord, ...]
]
_COVERAGE_ORDER = ("covered", "fringe", "uncovered")


@dataclass(frozen=True, slots=True)
class CoverageTransition:
    baseline: Coverage
    challenger: Coverage
    cafes: int


@dataclass(frozen=True, slots=True)
class DivergenceAuditCase:
    cafe_id: int
    cafe_name: str
    lat: float
    lng: float
    baseline_coverage: Coverage
    challenger_coverage: Coverage
    baseline_score: float
    challenger_score: float
    baseline_level: int
    challenger_level: int
    absolute_score_delta: float
    overlap_count: int


@dataclass(frozen=True, slots=True)
class StructuralShadowReport:
    baseline_model: str
    challenger_model: str
    geometry_versions: tuple[str, ...]
    as_of: datetime
    active_cafes: int
    transitions: tuple[CoverageTransition, ...]
    paired_scores: int
    changed_scores: int
    unchanged_scores: int
    mean_absolute_score_delta: float | None
    max_absolute_score_delta: float | None
    paired_levels: int
    changed_levels: int
    unchanged_levels: int
    overlap_contributor_cafes: int
    divergence_audit_cases: tuple[DivergenceAuditCase, ...]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def load_latest_observations(session: Session) -> tuple[HotspotObservation, ...]:
    """Load one latest cached snapshot per polled hotspot without mutation."""

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
            Hotspot.name,
            Hotspot.lat,
            Hotspot.lng,
            HotspotSnapshot.congest_level,
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
    return tuple(
        HotspotObservation(
            hotspot_id=hotspot_id,
            name=name,
            lat=lat,
            lng=lng,
            level=level,
            observed_at=_as_utc(observed_at),
        )
        for hotspot_id, name, lat, lng, level, observed_at in rows
    )


def select_divergence_audit_cases(
    cases: Sequence[DivergenceAuditCase],
    *,
    limit: int = SHADOW_DIVERGENCE_AUDIT_LIMIT,
) -> tuple[DivergenceAuditCase, ...]:
    if limit < 1:
        raise ValueError("divergence audit limit must be positive")
    return tuple(
        sorted(
            cases,
            key=lambda item: (-item.absolute_score_delta, item.cafe_id),
        )[:limit]
    )


def build_structural_shadow_report(
    session: Session,
    geometries: dict[int, PolygonHotspotGeometry],
) -> StructuralShadowReport:
    """Score every active cafe in memory; never persist either model output."""

    observations = load_latest_observations(session)
    if not observations:
        raise ValueError("no latest polled hotspot snapshots available")
    if set(geometries) != {item.hotspot_id for item in observations}:
        raise ValueError("latest snapshot and geometry hotspot sets differ")

    as_of = max(item.observed_at for item in observations)
    cafes = session.execute(
        select(Cafe.id, Cafe.name, Cafe.lat, Cafe.lng)
        .where(Cafe.active.is_(True))
        .order_by(Cafe.id)
    ).all()

    transition_counts = {
        (baseline, challenger): 0
        for baseline in _COVERAGE_ORDER
        for challenger in _COVERAGE_ORDER
    }
    absolute_score_deltas: list[float] = []
    changed_scores = 0
    changed_levels = 0
    paired_levels = 0
    overlaps = 0
    divergence_cases: list[DivergenceAuditCase] = []
    for cafe_id, cafe_name, cafe_lat, cafe_lng in cafes:
        baseline = score_cafe(
            cafe_lat,
            cafe_lng,
            observations,
            now=as_of,
        )
        challenger = score_cafe_polygon_shadow_compatible(
            cafe_lat,
            cafe_lng,
            observations,
            geometries,
            now=as_of,
        )
        transition_counts[(baseline.coverage, challenger.coverage)] += 1
        cafe_point = Point(cafe_lng, cafe_lat)
        overlap_count = sum(
            geometry.geometry.covers(cafe_point) for geometry in geometries.values()
        )
        if overlap_count > 1:
            overlaps += 1
        if baseline.score is not None and challenger.score is not None:
            delta = abs(challenger.score - baseline.score)
            absolute_score_deltas.append(delta)
            changed_scores += delta > 0.0
            if baseline.level is None or challenger.level is None:
                raise ValueError("paired scores require paired levels")
            divergence_cases.append(
                DivergenceAuditCase(
                    cafe_id=cafe_id,
                    cafe_name=cafe_name,
                    lat=cafe_lat,
                    lng=cafe_lng,
                    baseline_coverage=baseline.coverage,
                    challenger_coverage=challenger.coverage,
                    baseline_score=baseline.score,
                    challenger_score=challenger.score,
                    baseline_level=baseline.level,
                    challenger_level=challenger.level,
                    absolute_score_delta=delta,
                    overlap_count=overlap_count,
                )
            )
        if baseline.level is not None and challenger.level is not None:
            paired_levels += 1
            changed_levels += baseline.level != challenger.level

    paired_scores = len(absolute_score_deltas)
    transitions = tuple(
        CoverageTransition(baseline, challenger, transition_counts[(baseline, challenger)])
        for baseline in _COVERAGE_ORDER
        for challenger in _COVERAGE_ORDER
    )
    return StructuralShadowReport(
        baseline_model=SCORING_MODEL_VERSION,
        challenger_model=POLYGON_SHADOW_MODEL_VERSION,
        geometry_versions=tuple(
            sorted({geometry.geometry_version for geometry in geometries.values()})
        ),
        as_of=as_of,
        active_cafes=len(cafes),
        transitions=transitions,
        paired_scores=paired_scores,
        changed_scores=changed_scores,
        unchanged_scores=paired_scores - changed_scores,
        mean_absolute_score_delta=(
            mean(absolute_score_deltas) if absolute_score_deltas else None
        ),
        max_absolute_score_delta=(
            max(absolute_score_deltas) if absolute_score_deltas else None
        ),
        paired_levels=paired_levels,
        changed_levels=changed_levels,
        unchanged_levels=paired_levels - changed_levels,
        overlap_contributor_cafes=overlaps,
        divergence_audit_cases=select_divergence_audit_cases(divergence_cases),
    )


def _metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.6f}"


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: StructuralShadowReport) -> str:
    versions = ", ".join(f"`{item}`" for item in report.geometry_versions) or "N/A"
    lines = [
        "# Structural Shadow Snapshot",
        "",
        "> **NOT accuracy evidence.** This report has no ground truth. It only "
        "describes structural differences between two deterministic models.",
        "",
        f"- Baseline model: `{report.baseline_model}`",
        f"- Challenger model: `{report.challenger_model}`",
        f"- Geometry version(s): {versions}",
        f"- Snapshot as-of: `{report.as_of.isoformat()}`",
        f"- Active cafes: {report.active_cafes}",
        f"- Overlap-contributor cafes: {report.overlap_contributor_cafes}",
        "- Database writes: none",
        "",
        "## Coverage transition matrix",
        "",
        "| v1 coverage | v2 coverage | Cafes |",
        "| --- | --- | ---: |",
    ]
    lines.extend(
        f"| {item.baseline} | {item.challenger} | {item.cafes} |"
        for item in report.transitions
    )
    lines.extend(
        [
            "",
            "## Score and level changes",
            "",
            "| Measure | Value |",
            "| --- | ---: |",
            f"| Paired scores | {report.paired_scores} |",
            f"| Changed scores | {report.changed_scores} |",
            f"| Unchanged scores | {report.unchanged_scores} |",
            f"| Mean absolute score delta | "
            f"{_metric(report.mean_absolute_score_delta)} |",
            f"| Max absolute score delta | "
            f"{_metric(report.max_absolute_score_delta)} |",
            f"| Paired levels | {report.paired_levels} |",
            f"| Changed levels | {report.changed_levels} |",
            f"| Unchanged levels | {report.unchanged_levels} |",
        ]
    )
    lines.extend(
        [
            "",
            f"## Top {SHADOW_DIVERGENCE_AUDIT_LIMIT} divergence audit cases",
            "",
            "Read-only triage list, sorted by absolute score delta descending then "
            "cafe ID. These cases are not accuracy failures without ground truth.",
            "",
            "| Cafe ID | Name | Lat | Lng | v1 coverage | v1 score | v1 level | "
            "v2 coverage | v2 score | v2 level | Abs delta | Overlaps |",
            "| ---: | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | "
            "---: | ---: | ---: |",
        ]
    )
    for item in report.divergence_audit_cases:
        lines.append(
            f"| {item.cafe_id} | {_escape_cell(item.cafe_name)} | "
            f"{item.lat:.6f} | {item.lng:.6f} | {item.baseline_coverage} | "
            f"{item.baseline_score:.6f} | {item.baseline_level} | "
            f"{item.challenger_coverage} | {item.challenger_score:.6f} | "
            f"{item.challenger_level} | {item.absolute_score_delta:.6f} | "
            f"{item.overlap_count} |"
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
                report = build_structural_shadow_report(session, geometries)
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
