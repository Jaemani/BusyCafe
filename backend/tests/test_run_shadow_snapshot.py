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
from scripts.run_shadow_snapshot import (
    DivergenceAuditCase,
    build_structural_shadow_report,
    main,
    render_markdown,
    select_divergence_audit_cases,
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
        for hotspot_id, level in ((1, 1), (2, 4)):
            session.add(
                HotspotSnapshot(
                    hotspot_id=hotspot_id,
                    observed_at=NOW - timedelta(minutes=hotspot_id),
                    fetched_at=NOW,
                    congest_level=level,
                    congest_label=str(level),
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
                    name="겹|침\n카페",
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


def test_structural_snapshot_is_deterministic_complete_and_read_only(
    tmp_path: Path,
) -> None:
    database_url = _seed(tmp_path / "shadow.db")
    engine = create_engine(database_url)
    with Session(engine) as session:
        geometries = bind_hotspot_geometries(session, _records())
        before_snapshots = session.scalar(select(func.count(HotspotSnapshot.id)))
        before_scores = session.scalar(select(func.count(CafeScore.cafe_id)))
        first = build_structural_shadow_report(session, geometries)
        second = build_structural_shadow_report(session, geometries)
        after_snapshots = session.scalar(select(func.count(HotspotSnapshot.id)))
        after_scores = session.scalar(select(func.count(CafeScore.cafe_id)))
    engine.dispose()

    assert first == second
    assert first.active_cafes == 2
    assert first.as_of == NOW - timedelta(minutes=1)
    assert first.geometry_versions == ("fixture-geometry-v1",)
    assert sum(item.cafes for item in first.transitions) == 2
    transitions = {
        (item.baseline, item.challenger): item.cafes
        for item in first.transitions
    }
    assert transitions[("covered", "covered")] == 1
    assert transitions[("fringe", "covered")] == 1
    assert first.paired_scores == 2
    assert first.changed_scores == 1
    assert first.unchanged_scores == 1
    assert first.mean_absolute_score_delta == pytest.approx(0.75)
    assert first.max_absolute_score_delta == pytest.approx(1.5)
    assert first.paired_levels == 2
    assert first.changed_levels == 1
    assert first.unchanged_levels == 1
    assert first.overlap_contributor_cafes == 1
    assert [item.cafe_id for item in first.divergence_audit_cases] == [1, 2]
    top = first.divergence_audit_cases[0]
    assert top.cafe_name == "겹|침\n카페"
    assert top.lat == 0.0
    assert top.lng == 0.0
    assert top.baseline_coverage == "covered"
    assert top.challenger_coverage == "covered"
    assert top.baseline_score == pytest.approx(1.0)
    assert top.challenger_score == pytest.approx(2.5)
    assert top.baseline_level == 1
    assert top.challenger_level == 3
    assert top.absolute_score_delta == pytest.approx(1.5)
    assert top.overlap_count == 2
    assert before_snapshots == after_snapshots
    assert before_scores == after_scores

    markdown = render_markdown(first)
    assert "**NOT accuracy evidence.**" in markdown
    assert "- Database writes: none" in markdown
    assert "| fringe | covered | 1 |" in markdown
    assert "| Changed levels | 1 |" in markdown
    assert "## Top 20 divergence audit cases" in markdown
    assert "| 1 | 겹\\|침 카페 |" in markdown


def test_divergence_audit_sort_is_delta_desc_then_cafe_id_and_limited() -> None:
    def item(cafe_id: int, delta: float) -> DivergenceAuditCase:
        return DivergenceAuditCase(
            cafe_id=cafe_id,
            cafe_name=str(cafe_id),
            lat=37.0,
            lng=127.0,
            baseline_coverage="covered",
            challenger_coverage="covered",
            baseline_score=1.0,
            challenger_score=1.0 + delta,
            baseline_level=1,
            challenger_level=1,
            absolute_score_delta=delta,
            overlap_count=0,
        )

    ranked = select_divergence_audit_cases(
        [item(9, 1.0), item(3, 2.0), item(2, 1.0)],
        limit=2,
    )

    assert [case.cafe_id for case in ranked] == [3, 2]
    with pytest.raises(ValueError, match="limit must be positive"):
        select_divergence_audit_cases([], limit=0)


def test_structural_snapshot_requires_latest_snapshots(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(ValueError, match="no latest polled"):
            build_structural_shadow_report(session, {})
    engine.dispose()


def test_cli_prints_or_writes_without_mutating_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_url = _seed(tmp_path / "shadow.db")

    def loader(_list: str | Path, _areas: str | Path):
        return _records()

    assert main(["--database-url", database_url], geometry_loader=loader) == 0
    assert "# Structural Shadow Snapshot" in capsys.readouterr().out

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
        "# Structural Shadow Snapshot"
    )
