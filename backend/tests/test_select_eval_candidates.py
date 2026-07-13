from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import SCORING_MODEL_VERSION
from app.models import Base, Cafe, CafeScore, Hotspot
from scripts.select_eval_candidates import main, select_candidates


NOW = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)


def _add_candidate(
    session: Session,
    *,
    cafe_id: int,
    hotspot_id: int,
    distance_m: float,
    confidence: float,
    active: bool = True,
) -> None:
    cafe = Cafe(
        id=cafe_id,
        overture_id=f"overture:{cafe_id}",
        source_release="2026-06-17.0",
        source_confidence=confidence,
        primary_category="cafe",
        name=f"카페 {cafe_id}",
        road_address=f"주소 {cafe_id}",
        lat=37.0 + cafe_id / 100_000,
        lng=127.0,
        active=active,
    )
    session.add(cafe)
    session.flush()
    coverage = "covered" if distance_m <= 600 else "fringe"
    session.add(
        CafeScore(
            cafe_id=cafe.id,
            model_version=SCORING_MODEL_VERSION,
            computed_at=NOW,
            source_observed_at=NOW,
            score=2.0,
            level=2,
            confidence=0.5,
            confidence_tier="mid",
            coverage=coverage,
            primary_hotspot_id=hotspot_id,
            primary_distance_m=distance_m,
            contributors_json=[],
        )
    )


def _seed_database(path: Path) -> str:
    database_url = f"sqlite+pysqlite:///{path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Hotspot(
                    id=1,
                    area_cd="POI001",
                    name="홍대 관광특구",
                    lat=37.0,
                    lng=127.0,
                    is_polled=True,
                ),
                Hotspot(
                    id=2,
                    area_cd="POI002",
                    name="성수카페거리",
                    lat=37.0,
                    lng=127.1,
                    is_polled=True,
                ),
                Hotspot(
                    id=3,
                    area_cd="POI003",
                    name="경계 테스트",
                    lat=37.0,
                    lng=127.2,
                    is_polled=True,
                ),
            ]
        )
        session.flush()
        for cafe_id, distance, confidence in (
            (1, 100.0, 0.90),
            (2, 200.0, 0.95),
            (3, 150.0, 0.95),
            (4, 150.0, 0.95),
            (5, 50.0, 0.80),
        ):
            _add_candidate(
                session,
                cafe_id=cafe_id,
                hotspot_id=1,
                distance_m=distance,
                confidence=confidence,
            )
        for cafe_id, distance in enumerate(
            (300.0, 300.1, 600.0, 600.1, 1_500.0, 1_500.1), start=10
        ):
            _add_candidate(
                session,
                cafe_id=cafe_id,
                hotspot_id=3,
                distance_m=distance,
                confidence=0.9,
            )
        _add_candidate(
            session,
            cafe_id=20,
            hotspot_id=3,
            distance_m=100.0,
            confidence=1.0,
            active=False,
        )
        session.commit()
    engine.dispose()
    return database_url


def test_default_selection_is_capped_and_deterministically_ordered(
    tmp_path: Path,
) -> None:
    engine = create_engine(_seed_database(tmp_path / "candidates.db"))
    with Session(engine) as session:
        result = select_candidates(session)
    engine.dispose()

    assert [item.cafe_id for item in result.candidates] == [3, 4, 2, 1]
    assert all(item.hotspot_name == "홍대 관광특구" for item in result.candidates)
    assert ("홍대 관광특구", "near", 4, 4) not in result.shortages
    assert ("홍대 관광특구", "mid", 0, 4) in result.shortages
    assert ("성수카페거리", "near", 0, 4) in result.shortages


def test_distance_boundaries_and_active_filter_are_strict(tmp_path: Path) -> None:
    engine = create_engine(_seed_database(tmp_path / "candidates.db"))
    with Session(engine) as session:
        result = select_candidates(session, ["경계 테스트"], per_band=10)
    engine.dispose()

    assert [(item.cafe_id, item.distance_band) for item in result.candidates] == [
        (10, "near"),
        (11, "mid"),
        (12, "mid"),
        (13, "fringe"),
        (14, "fringe"),
    ]
    assert all(item.cafe_id not in {15, 20} for item in result.candidates)


def test_invalid_selection_parameters_fail_closed(tmp_path: Path) -> None:
    engine = create_engine(_seed_database(tmp_path / "candidates.db"))
    with Session(engine) as session:
        with pytest.raises(ValueError, match="per_band"):
            select_candidates(session, ["홍대 관광특구"], per_band=0)
        with pytest.raises(ValueError, match="hotspot"):
            select_candidates(session, [])
    engine.dispose()


def test_cli_uses_stdout_by_default_and_reports_shortages_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_url = _seed_database(tmp_path / "candidates.db")

    assert main(["--database-url", database_url]) == 0
    captured = capsys.readouterr()
    rows = list(csv.DictReader(io.StringIO(captured.out)))
    assert len(rows) == 4
    assert tuple(rows[0]) == (
        "cafe_id",
        "name",
        "road_address",
        "lat",
        "lng",
        "hotspot_name",
        "distance_band",
        "primary_distance_m",
        "source_confidence",
        "poi_valid",
        "exclusion_reason",
    )
    assert rows[0]["poi_valid"] == ""
    assert rows[0]["exclusion_reason"] == ""
    assert "shortage: hotspot='성수카페거리' band=near selected=0/4" in captured.err
    assert list(tmp_path.glob("*.csv")) == []

    output = tmp_path / "field_candidates.csv"
    assert (
        main(
            [
                "--database-url",
                database_url,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert output.read_text(encoding="utf-8").startswith("cafe_id,name,")
    assert "shortage:" in captured.err
