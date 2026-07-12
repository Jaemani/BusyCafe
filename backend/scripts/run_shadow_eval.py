"""Compare public point-IDW with the offline polygon shadow model."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    POLYGON_SHADOW_MODEL_VERSION,
    SEOUL_HOTSPOT_AREAS_PATH,
    SEOUL_HOTSPOT_LIST_PATH,
)
from app.database import create_db_engine
from app.ingest.hotspot_master import (
    HotspotGeometryRecord,
    load_hotspot_geometry_master,
)
from app.models import Hotspot
from app.scoring.polygon_shadow import (
    PolygonHotspotGeometry,
    score_cafe_polygon_shadow_compatible,
)
from scripts.run_eval import (
    EvaluationScorer,
    GroundTruth,
    ShadowComparison,
    compare_evaluation_reports,
    evaluate,
    load_observations,
    render_shadow_markdown,
)


GeometryLoader = Callable[
    [str | Path, str | Path], tuple[HotspotGeometryRecord, ...]
]


def bind_hotspot_geometries(
    session: Session,
    records: Sequence[HotspotGeometryRecord],
) -> dict[int, PolygonHotspotGeometry]:
    """Join verified official geometry to stable DB hotspot ids."""

    by_area_code = {record.area_cd: record for record in records}
    if len(by_area_code) != len(records):
        raise ValueError("duplicate area_cd in polygon geometry records")

    rows = session.execute(
        select(Hotspot.id, Hotspot.area_cd, Hotspot.name)
        .where(Hotspot.is_polled.is_(True))
        .order_by(Hotspot.id)
    ).all()
    database_codes = {row.area_cd for row in rows}
    geometry_codes = set(by_area_code)
    if database_codes != geometry_codes:
        missing = ", ".join(sorted(database_codes - geometry_codes)) or "none"
        extra = ", ".join(sorted(geometry_codes - database_codes)) or "none"
        raise ValueError(
            "hotspot/geometry AREA_CD sets differ; "
            f"missing geometry: {missing}; extra geometry: {extra}"
        )

    bindings: dict[int, PolygonHotspotGeometry] = {}
    for row in rows:
        record = by_area_code[row.area_cd]
        if row.name != record.name:
            raise ValueError(f"hotspot/geometry name mismatch: {row.area_cd}")
        bindings[row.id] = PolygonHotspotGeometry(
            area_cd=record.area_cd,
            name=record.name,
            geometry_version=record.geometry_version,
            geometry_normalization=record.normalization,
            geometry=record.geometry,
        )
    return bindings


def make_polygon_scorer(
    geometries: Mapping[int, PolygonHotspotGeometry],
) -> EvaluationScorer:
    """Close immutable geometry over the generic historical evaluator."""

    def scorer(cafe, observations, observed_at):
        return score_cafe_polygon_shadow_compatible(
            cafe.lat,
            cafe.lng,
            observations,
            geometries,
            now=observed_at,
        )

    return scorer


def evaluate_polygon_shadow(
    session: Session,
    truths: Sequence[GroundTruth],
    *,
    total_rows: int,
    invalid_rows: int,
    geometry_records: Sequence[HotspotGeometryRecord],
) -> ShadowComparison:
    """Replay the same truths and historical snapshots through both models."""

    geometries = bind_hotspot_geometries(session, geometry_records)
    baseline = evaluate(
        session,
        truths,
        total_rows=total_rows,
        invalid_rows=invalid_rows,
    )
    challenger = evaluate(
        session,
        truths,
        total_rows=total_rows,
        invalid_rows=invalid_rows,
        scorer=make_polygon_scorer(geometries),
        model_version=POLYGON_SHADOW_MODEL_VERSION,
    )
    return compare_evaluation_reports(baseline, challenger)


def main(
    argv: Sequence[str] | None = None,
    *,
    geometry_loader: GeometryLoader = load_hotspot_geometry_master,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path, help="ground-truth CSV")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument("--output", type=Path, help="write paired Markdown report")
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
        truths, total, invalid = load_observations(args.observations)
        geometry_records = geometry_loader(args.hotspot_list, args.hotspot_areas)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        parser.error(str(exc))

    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            comparison = evaluate_polygon_shadow(
                session,
                truths,
                total_rows=total,
                invalid_rows=invalid,
                geometry_records=geometry_records,
            )
    finally:
        engine.dispose()

    markdown = render_shadow_markdown(comparison)
    if args.output is None:
        print(markdown, end="")
    else:
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
