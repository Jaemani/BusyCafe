from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.ingest.overture_places import (
    OvertureCafeRecord,
    OvertureIngestError,
    parse_overture_row,
    seed_overture_cafes,
)
from app.models import Base, Cafe


def record(identifier: str = "overture:test-1", **overrides: object) -> OvertureCafeRecord:
    values: dict[str, object] = {
        "overture_id": identifier,
        "name": "테스트 카페",
        "lat": 37.55,
        "lng": 126.98,
        "primary_category": "cafe",
        "confidence": 0.9,
        "road_address": "서울시 테스트구 1",
        "phone": "02-123-4567",
        "website": "https://example.test",
        "sources": [{"dataset": "test"}],
    }
    values.update(overrides)
    return OvertureCafeRecord(**values)  # type: ignore[arg-type]


@pytest.fixture
def engine():
    db_engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    yield db_engine
    db_engine.dispose()


def test_parse_overture_row_preserves_source_and_normalizes_optional_text() -> None:
    parsed = parse_overture_row(
        {
            "overture_id": "  overture:1 ",
            "name": "  카페  ",
            "lat": 37.55,
            "lng": 126.98,
            "primary_category": "cafe",
            "confidence": 0.8,
            "road_address": " ",
            "phone": None,
            "website": "https://example.test",
            "sources_json": '[{"dataset":"meta"}]',
        }
    )

    assert parsed.overture_id == "overture:1"
    assert parsed.name == "카페"
    assert parsed.road_address is None
    assert parsed.sources == [{"dataset": "meta"}]


@pytest.mark.parametrize(
    "row",
    [
        {"overture_id": "x"},
        {
            "overture_id": "x",
            "name": "x",
            "lat": 100,
            "lng": 127,
            "primary_category": "cafe",
            "confidence": 0.8,
        },
        {
            "overture_id": "x",
            "name": "x",
            "lat": 37,
            "lng": 127,
            "primary_category": "cafe",
            "confidence": 1.1,
        },
    ],
)
def test_parse_overture_row_rejects_invalid_data(row: dict[str, object]) -> None:
    with pytest.raises(OvertureIngestError):
        parse_overture_row(row)


def test_seed_is_idempotent_and_deactivates_missing_records(engine) -> None:
    with Session(engine) as session:
        first = seed_overture_cafes(
            session,
            [record("overture:1"), record("overture:2", name="둘")],
            release="2026-06-17.0",
        )
        second = seed_overture_cafes(
            session,
            [record("overture:1"), record("overture:2", name="둘")],
            release="2026-06-17.0",
        )
        third = seed_overture_cafes(
            session,
            [record("overture:1", name="바뀐 이름")],
            release="2026-07-01.0",
        )

        assert first.inserted_count == 2
        assert second.unchanged_count == 2
        assert third.updated_count == 1
        assert third.deactivated_count == 1
        assert session.scalar(select(func.count()).select_from(Cafe)) == 2
        assert session.scalar(select(Cafe).where(Cafe.overture_id == "overture:1")).name == "바뀐 이름"
        assert session.scalar(select(Cafe).where(Cafe.overture_id == "overture:2")).active is False


def test_dry_run_has_no_database_effect_and_duplicate_ids_fail(engine) -> None:
    with Session(engine) as session:
        report = seed_overture_cafes(
            session,
            [record()],
            release="2026-06-17.0",
            dry_run=True,
        )
        assert report.inserted_count == 1
        assert session.scalar(select(func.count()).select_from(Cafe)) == 0
        with pytest.raises(OvertureIngestError, match="duplicate"):
            seed_overture_cafes(
                session,
                [record(), record()],
                release="2026-06-17.0",
            )
