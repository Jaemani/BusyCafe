from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models import Base, Hotspot, HotspotSnapshot, IngestCycle
from scripts.analyze_ingest_slo import (
    _estimated_missed_cycles,
    analyze_ingest_slo,
    enforce_transaction_read_only,
)


NOW = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("gap_min", "expected"),
    [(10.01, 1), (14.99, 2)],
)
def test_missed_cycle_estimator_tolerates_cadence_jitter(
    gap_min: float, expected: int
) -> None:
    assert _estimated_missed_cycles(gap_min, 5) == expected


def add_cycle(
    session: Session,
    *,
    cycle_id: int,
    minute: int,
    status: str,
    saved: int,
    failed: int,
    duration_min: int | None,
) -> None:
    started_at = NOW.replace(hour=0, minute=minute)
    session.add(
        IngestCycle(
            id=cycle_id,
            started_at=started_at,
            completed_at=(
                started_at + timedelta(minutes=duration_min)
                if duration_min is not None
                else None
            ),
            targets=2,
            saved=saved,
            failed=failed,
            status=status,
        )
    )


def seeded_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = Hotspot(
            area_cd="POI001",
            name="첫 지역",
            lat=37.5,
            lng=127.0,
            is_polled=True,
        )
        second = Hotspot(
            area_cd="POI002",
            name="둘째 지역",
            lat=37.6,
            lng=127.1,
            is_polled=True,
        )
        session.add_all([first, second])
        session.flush()
        add_cycle(
            session,
            cycle_id=1,
            minute=0,
            status="complete",
            saved=2,
            failed=0,
            duration_min=2,
        )
        add_cycle(
            session,
            cycle_id=2,
            minute=5,
            status="complete",
            saved=2,
            failed=0,
            duration_min=2,
        )
        add_cycle(
            session,
            cycle_id=3,
            minute=20,
            status="partial",
            saved=1,
            failed=1,
            duration_min=1,
        )
        add_cycle(
            session,
            cycle_id=4,
            minute=25,
            status="failed",
            saved=0,
            failed=2,
            duration_min=1,
        )
        add_cycle(
            session,
            cycle_id=5,
            minute=30,
            status="running",
            saved=0,
            failed=0,
            duration_min=None,
        )
        session.add_all(
            [
                HotspotSnapshot(
                    hotspot_id=first.id,
                    observed_at=NOW - timedelta(hours=1, minutes=30),
                    fetched_at=NOW - timedelta(minutes=59),
                    congest_level=2,
                    congest_label="보통",
                    ppltn_min=100,
                    ppltn_max=200,
                    forecast_json=[],
                ),
                HotspotSnapshot(
                    hotspot_id=second.id,
                    observed_at=NOW - timedelta(hours=1, minutes=9),
                    fetched_at=NOW - timedelta(minutes=59),
                    congest_level=2,
                    congest_label="보통",
                    ppltn_min=100,
                    ppltn_max=200,
                    forecast_json=[],
                ),
                HotspotSnapshot(
                    hotspot_id=first.id,
                    observed_at=NOW - timedelta(hours=1, minutes=39, seconds=30),
                    fetched_at=NOW - timedelta(minutes=39, seconds=30),
                    congest_level=3,
                    congest_label="약간 붐빔",
                    ppltn_min=200,
                    ppltn_max=300,
                    forecast_json=[],
                ),
            ]
        )
        session.commit()
    return engine


def test_analyzer_reports_cycle_cadence_lag_signal_and_coverage() -> None:
    engine = seeded_engine()
    with Session(engine) as session:
        report = analyze_ingest_slo(
            session,
            as_of=NOW,
            window_hours=1,
            expected_cadence_min=5,
            cadence_gap_factor=2,
        )
        repeated = analyze_ingest_slo(
            session,
            as_of=NOW,
            window_hours=1,
            expected_cadence_min=5,
            cadence_gap_factor=2,
        )
    engine.dispose()

    assert report == repeated
    assert report["schema_version"] == 2
    assert report["cycles"]["status_counts"] == {
        "complete": 2,
        "partial": 1,
        "failed": 1,
        "running": 1,
    }
    assert report["cycles"]["complete_rate"] == pytest.approx(0.5)
    assert report["cycles"]["target_success_rate"] == pytest.approx(0.625)
    assert report["cycles"]["duration_seconds"] == {
        "samples": 4,
        "p50": 90.0,
        "p95": 120.0,
        "max": 120.0,
        "invalid_or_missing": 0,
    }
    assert report["cadence"]["gap_count"] == 2
    assert [item["kind"] for item in report["cadence"]["gaps"]] == [
        "between_cycles",
        "window_tail",
    ]
    assert report["cadence"]["estimated_missed_cycles"] == 7
    lag = report["snapshot_observed_lag_minutes"]
    assert lag["samples"] == 3
    assert lag["p50"] == pytest.approx(31)
    assert lag["p95"] == pytest.approx(57.1)
    assert lag["max"] == pytest.approx(60)
    assert lag["negative_lag_count"] == 0
    signal = report["cycle_snapshot_signal"]
    assert signal["direct_persisted_insert_counter"] is False
    assert signal["signal_counts"] == {
        "duplicate_or_unchanged": 1,
        "new_data": 2,
        "no_new_data_failure": 1,
        "pending": 1,
    }
    assert "cycles" not in signal
    assert signal["anomalous_terminal_cycles"] == [
        {
            "id": 3,
            "started_at": "2026-07-14T00:20:00Z",
            "completed_at": "2026-07-14T00:21:00Z",
            "targets": 2,
            "saved": 1,
            "failed": 1,
            "status": "partial",
            "duration_seconds": 60.0,
            "new_snapshot_count": 1,
            "signal": "new_data",
        },
        {
            "id": 4,
            "started_at": "2026-07-14T00:25:00Z",
            "completed_at": "2026-07-14T00:26:00Z",
            "targets": 2,
            "saved": 0,
            "failed": 2,
            "status": "failed",
            "duration_seconds": 60.0,
            "new_snapshot_count": 0,
            "signal": "no_new_data_failure",
        },
    ]
    coverage = report["hotspot_coverage"]
    assert coverage["coverage_rate"] == pytest.approx(1)
    assert coverage["missing_hotspots"] == []
    assert coverage["snapshot_count_per_seen_hotspot"]["p50"] == pytest.approx(
        1.5
    )


def test_analyzer_executes_selects_only() -> None:
    engine = seeded_engine()
    statements: list[str] = []

    def capture_statement(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        statements.append(statement.strip().upper())

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        with Session(engine) as session:
            analyze_ingest_slo(session, as_of=NOW, window_hours=1)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
        engine.dispose()

    assert statements
    assert all(statement.startswith("SELECT") for statement in statements)


def test_postgresql_transaction_is_explicitly_read_only_and_fail_closed() -> None:
    class Dialect:
        name = "postgresql"

    class Bind:
        dialect = Dialect()

    class RecordingSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def get_bind(self) -> Bind:
            return Bind()

        def execute(self, statement) -> None:
            self.statements.append(str(statement))

    session = RecordingSession()
    enforce_transaction_read_only(session)

    assert session.statements == ["SET TRANSACTION READ ONLY"]

    class RejectingSession(RecordingSession):
        def execute(self, statement) -> None:
            raise RuntimeError("read-only guard rejected")

    with pytest.raises(RuntimeError, match="read-only guard rejected"):
        enforce_transaction_read_only(RejectingSession())


def test_analyzer_handles_empty_database_without_dividing_by_zero() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        report = analyze_ingest_slo(session, as_of=NOW, window_hours=1)
    engine.dispose()

    assert report["cycles"]["complete_rate"] is None
    assert report["cycle_snapshot_signal"]["anomalous_terminal_cycles"] == []
    assert report["cadence"]["gap_count"] == 1
    assert report["cadence"]["gaps"][0]["kind"] == "empty_window"
    assert report["snapshot_observed_lag_minutes"]["samples"] == 0
    assert report["hotspot_coverage"]["coverage_rate"] is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"window_hours": 0}, "window_hours must be positive"),
        ({"expected_cadence_min": 0}, "expected_cadence_min must be positive"),
        ({"cadence_gap_factor": 0.5}, "cadence_gap_factor must be at least one"),
    ],
)
def test_analyzer_rejects_invalid_parameters(kwargs: dict, message: str) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session, pytest.raises(ValueError, match=message):
        analyze_ingest_slo(session, as_of=NOW, **kwargs)
    engine.dispose()


def test_analyzer_rejects_timezone_less_as_of() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session, pytest.raises(
        ValueError, match="as_of must include a timezone"
    ):
        analyze_ingest_slo(session, as_of=NOW.replace(tzinfo=None))
    engine.dispose()
