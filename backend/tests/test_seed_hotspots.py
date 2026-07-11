from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import MAX_POLLED_HOTSPOTS, Neighborhood
from app.geo import haversine_m
from app.ingest.hotspot_master import load_hotspot_master
from app.models import Base, Hotspot
from scripts.seed_hotspots import (
    HotspotSeedError,
    format_report,
    main,
    seed_hotspots,
    select_polled_hotspots,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
XLSX = FIXTURES / "seoul_hotspots_master.xlsx"
SHAPEFILE_ZIP = FIXTURES / "seoul_hotspot_areas.zip"
EXPECTED_POLLED_CODES = (
    "POI007",
    "POI015",
    "POI025",
    "POI040",
    "POI053",
    "POI055",
    "POI068",
    "POI073",
    "POI101",
    "POI122",
)


@pytest.fixture
def engine():
    db_engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(scope="module")
def official_records():
    return load_hotspot_master(XLSX, SHAPEFILE_ZIP)


def test_haversine_is_deterministic_symmetric_and_zero_for_same_point() -> None:
    assert haversine_m(37.5665, 126.9780, 37.5665, 126.9780) == 0.0
    distance = haversine_m(37.5665, 126.9780, 37.5446, 127.0557)
    reverse = haversine_m(37.5446, 127.0557, 37.5665, 126.9780)
    assert distance == pytest.approx(reverse)
    assert distance == pytest.approx(7_269.4, abs=1.0)


def test_actual_master_selects_exact_expected_polling_union(official_records) -> None:
    selected = select_polled_hotspots(official_records)

    assert len(selected) == 10
    assert tuple(row.area_cd for row in selected) == EXPECTED_POLLED_CODES
    assert len(selected) <= MAX_POLLED_HOTSPOTS
    assert all(row.neighborhoods for row in selected)


def test_maximum_polling_target_count_is_enforced(official_records) -> None:
    all_seoul = {
        "all": Neighborhood(lat=37.56, lng=126.98, radius_m=50_000),
    }

    with pytest.raises(HotspotSeedError, match="exceeding MAX_POLLED_HOTSPOTS=12"):
        select_polled_hotspots(official_records, neighborhoods=all_seoul)


def test_seed_is_idempotent_and_upserts_all_121_hotspots(
    engine, official_records
) -> None:
    with Session(engine) as session:
        first = seed_hotspots(session, official_records)
        second = seed_hotspots(session, official_records)

        assert first.inserted_count == 121
        assert first.updated_count == 0
        assert second.inserted_count == 0
        assert second.updated_count == 0
        assert second.unchanged_count == 121
        assert session.scalar(select(func.count()).select_from(Hotspot)) == 121
        assert tuple(
            session.scalars(
                select(Hotspot.area_cd)
                .where(Hotspot.is_polled.is_(True))
                .order_by(Hotspot.area_cd)
            )
        ) == EXPECTED_POLLED_CODES


def test_seed_updates_changed_fields_without_creating_duplicates(
    engine, official_records
) -> None:
    with Session(engine) as session:
        seed_hotspots(session, official_records)
        hotspot = session.scalar(select(Hotspot).where(Hotspot.area_cd == "POI001"))
        assert hotspot is not None
        hotspot.name = "오래된 이름"
        hotspot.is_polled = True
        session.commit()

        report = seed_hotspots(session, official_records)

        assert report.inserted_count == 0
        assert report.updated_count == 1
        assert hotspot.name == "강남 MICE 관광특구"
        assert hotspot.is_polled is False
        assert session.scalar(select(func.count()).select_from(Hotspot)) == 121


def test_extra_database_code_aborts_rolls_back_and_mutates_nothing(
    engine, official_records
) -> None:
    with Session(engine) as session:
        seed_hotspots(session, official_records)
        session.add(
            Hotspot(
                area_cd="LOCAL999",
                name="수동 추가 장소",
                category="manual",
                lat=37.55,
                lng=126.98,
                is_polled=True,
            )
        )
        session.commit()

        official = session.scalar(
            select(Hotspot).where(Hotspot.area_cd == "POI001")
        )
        assert official is not None
        official.name = "미커밋 변경"

        with pytest.raises(
            HotspotSeedError,
            match=(
                "database contains AREA_CD values absent from the official "
                "master: LOCAL999"
            ),
        ):
            seed_hotspots(session, official_records)

        assert session.scalar(select(func.count()).select_from(Hotspot)) == 122
        assert session.get(Hotspot, official.id).name == "강남 MICE 관광특구"
        extra = session.scalar(
            select(Hotspot).where(Hotspot.area_cd == "LOCAL999")
        )
        assert extra is not None
        assert extra.name == "수동 추가 장소"
        assert extra.is_polled is True


def test_dry_run_reports_without_writing_and_prints_manual_review(
    engine, official_records
) -> None:
    with Session(engine) as session:
        report = seed_hotspots(session, official_records, dry_run=True)
        rendered = format_report(report)

        assert report.inserted_count == 121
        assert session.scalar(select(func.count()).select_from(Hotspot)) == 0
        assert "mode: dry-run" in rendered
        assert "polling targets: 10/12" in rendered
        assert "manual review required:" in rendered
        assert "POI007 | 홍대 관광특구" in rendered


def test_cli_defaults_to_dry_run_and_main_without_args_writes_nothing(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'dry-run.db'}"
    db_engine = create_engine(database_url)
    Base.metadata.create_all(db_engine)
    db_engine.dispose()
    monkeypatch.setattr(
        "scripts.seed_hotspots.create_db_engine",
        lambda database_url_override: create_engine(database_url),
    )

    assert main([]) == 0

    output = capsys.readouterr().out
    assert "mode: dry-run" in output
    assert "source records: 121" in output
    check_engine = create_engine(database_url)
    with Session(check_engine) as session:
        assert session.scalar(select(func.count()).select_from(Hotspot)) == 0
    check_engine.dispose()


def test_cli_apply_writes_all_verified_records(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'apply.db'}"
    db_engine = create_engine(database_url)
    Base.metadata.create_all(db_engine)
    db_engine.dispose()

    assert main(["--database-url", database_url, "--apply"]) == 0

    output = capsys.readouterr().out
    assert "mode: write" in output
    check_engine = create_engine(database_url)
    with Session(check_engine) as session:
        assert session.scalar(select(func.count()).select_from(Hotspot)) == 121
        assert session.scalar(
            select(func.count())
            .select_from(Hotspot)
            .where(Hotspot.is_polled.is_(True))
        ) == 10
    check_engine.dispose()
