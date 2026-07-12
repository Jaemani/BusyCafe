from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from shapely.geometry import box
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.ingest.hotspot_master import HotspotGeometryRecord
from app.models import Base, Cafe, CafeScore, Hotspot, HotspotSnapshot
from scripts.run_shadow_eval import bind_hotspot_geometries
from scripts.run_density_snapshot import (
    build_density_structural_report,
    main,
    render_markdown,
)


NOW = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


def _records() -> tuple[HotspotGeometryRecord, ...]:
    return (
        HotspotGeometryRecord(
            area_cd="POI001",
            name="왼쪽",
            category="fixture",
            geometry_version="fixture-geometry-v1",
            normalization="original",
            geometry=box(-0.001, -0.001, 0.001, 0.001),
        ),
        HotspotGeometryRecord(
            area_cd="POI002",
            name="오른쪽",
            category="fixture",
            geometry_version="fixture-geometry-v1",
            normalization="original",
            geometry=box(-0.0005, -0.001, 0.0295, 0.001),
        ),
    )


def _seed(path: Path) -> str:
    database_url = f"sqlite+pysqlite:///{path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Hotspot(
                    id=1,
                    area_cd="POI001",
                    name="왼쪽",
                    category="fixture",
                    lat=0.0,
                    lng=0.0,
                    is_polled=True,
                ),
                Hotspot(
                    id=2,
                    area_cd="POI002",
                    name="오른쪽",
                    category="fixture",
                    lat=0.0,
                    lng=0.02,
                    is_polled=True,
                ),
            ]
        )
        for hotspot_id, ppltn_min, ppltn_max in ((1, 1000, 2000), (2, 4000, 6000)):
            session.add(
                HotspotSnapshot(
                    hotspot_id=hotspot_id,
                    observed_at=NOW - timedelta(minutes=hotspot_id),
                    fetched_at=NOW,
                    congest_level=2,
                    congest_label="보통",
                    ppltn_min=ppltn_min,
                    ppltn_max=ppltn_max,
                )
            )
        session.add_all(
            [
                Cafe(
                    id=1,
                    overture_id="active-overlap",
                    source_release="fixture",
                    source_confidence=1.0,
                    primary_category="cafe",
                    name="겹침카페",
                    lat=0.0,
                    lng=0.0,
                    active=True,
                ),
                Cafe(
                    id=2,
                    overture_id="active-boundary",
                    source_release="fixture",
                    source_confidence=1.0,
                    primary_category="cafe",
                    name="경계",
                    lat=0.0,
                    lng=0.03,
                    active=True,
                ),
                Cafe(
                    id=3,
                    overture_id="inactive",
                    source_release="fixture",
                    source_confidence=1.0,
                    primary_category="cafe",
                    name="비활성",
                    lat=0.0,
                    lng=0.0,
                    active=False,
                ),
            ]
        )
        session.commit()
    engine.dispose()
    return database_url


def test_density_snapshot_is_deterministic_complete_and_read_only(
    tmp_path: Path,
) -> None:
    database_url = _seed(tmp_path / "density.db")
    engine = create_engine(database_url)
    with Session(engine) as session:
        geometries = bind_hotspot_geometries(session, _records())
        before_snapshots = session.scalar(select(func.count(HotspotSnapshot.id)))
        before_scores = session.scalar(select(func.count(CafeScore.cafe_id)))
        first = build_density_structural_report(session, geometries)
        second = build_density_structural_report(session, geometries)
        after_snapshots = session.scalar(select(func.count(HotspotSnapshot.id)))
        after_scores = session.scalar(select(func.count(CafeScore.cafe_id)))
    engine.dispose()

    assert first == second
    assert first.model_version == "v3-density-shadow"
    assert first.active_cafes == 2
    assert first.as_of == NOW - timedelta(minutes=1)
    assert first.geometry_versions == ("fixture-geometry-v1",)
    assert first.hotspots_total == 2
    assert first.hotspots_missing_ppltn == 0

    coverage = dict(first.coverage_counts)
    selection = dict(first.selection_counts)
    assert sum(coverage.values()) == 2
    assert coverage["covered"] == 2
    assert coverage["uncovered"] == 0
    assert selection["inside"] == 1
    assert selection["boundary"] == 1

    assert first.scored_cafes == 2
    assert first.overlap_contributor_cafes == 1
    assert first.min_density_per_m2 is not None
    assert first.max_density_per_m2 is not None
    assert 0.0 < first.min_density_per_m2 <= first.max_density_per_m2
    assert (
        first.min_density_per_m2
        <= first.median_density_per_m2
        <= first.max_density_per_m2
    )
    assert before_snapshots == after_snapshots
    assert before_scores == after_scores

    markdown = render_markdown(first)
    assert "**NOT accuracy evidence.**" in markdown
    assert "- Database writes: none" in markdown
    assert "`v3-density-shadow`" in markdown
    assert "| inside | 1 |" in markdown


def test_density_snapshot_requires_latest_snapshots() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(ValueError, match="no latest polled|hotspot sets differ"):
            build_density_structural_report(session, {})
    engine.dispose()


def test_cli_prints_or_writes_without_mutating_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_url = _seed(tmp_path / "density.db")

    def loader(_list: str | Path, _areas: str | Path):
        return _records()

    assert main(["--database-url", database_url], geometry_loader=loader) == 0
    assert "# Density Structural Snapshot" in capsys.readouterr().out

    output = tmp_path / "snapshot.md"
    assert (
        main(
            ["--database-url", database_url, "--output", str(output)],
            geometry_loader=loader,
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    assert output.read_text(encoding="utf-8").startswith(
        "# Density Structural Snapshot"
    )
