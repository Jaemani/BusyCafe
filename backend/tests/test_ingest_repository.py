from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingest.poller import (
    ParseFailureRecord,
    PollTarget,
    SnapshotRecord,
    build_snapshot_record,
)
from app.ingest.repository import SnapshotRepository
from app.ingest.worker import main, run_poll_cycle, suppress_secret_bearing_http_logs
from app.models import (
    Base,
    Hotspot,
    HotspotParseFailure,
    HotspotSnapshot,
    IngestCycle,
)


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    yield factory
    engine.dispose()


def load_citydata_fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURES_DIR / "citydata_sample.json").read_text(encoding="utf-8")
    )


def add_hotspot(
    factory: sessionmaker[Session],
    *,
    area_code: str,
    name: str,
    is_polled: bool,
) -> int:
    with factory() as session:
        hotspot = Hotspot(
            area_cd=area_code,
            name=name,
            lat=37.57,
            lng=126.98,
            is_polled=is_polled,
        )
        session.add(hotspot)
        session.commit()
        return hotspot.id


def record_for(hotspot_id: int) -> SnapshotRecord:
    return build_snapshot_record(
        PollTarget(hotspot_id, "POI088", "광화문광장"),
        load_citydata_fixture(),
        fetched_at=datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
    )


class FixtureClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def fetch_population_raw(self, area_name: str) -> dict[str, Any]:
        self.calls.append(area_name)
        return copy.deepcopy(self.payload)


def test_repository_loads_only_polled_targets_in_stable_order(session_factory):
    second_id = add_hotspot(
        session_factory,
        area_code="POI002",
        name="둘째 대상",
        is_polled=True,
    )
    add_hotspot(
        session_factory,
        area_code="POI003",
        name="미대상",
        is_polled=False,
    )
    first_id = add_hotspot(
        session_factory,
        area_code="POI001",
        name="첫째 대상",
        is_polled=True,
    )

    targets = SnapshotRepository(session_factory).load_poll_targets()

    assert targets == [
        PollTarget(second_id, "POI002", "둘째 대상"),
        PollTarget(first_id, "POI001", "첫째 대상"),
    ]


def test_repository_persists_json_values_and_duplicate_is_noop(session_factory):
    hotspot_id = add_hotspot(
        session_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )
    repository = SnapshotRepository(session_factory)
    record = record_for(hotspot_id)

    repository.save_snapshot(record)
    repository.save_snapshot(record)

    with session_factory() as session:
        snapshots = session.scalars(select(HotspotSnapshot)).all()
        assert len(snapshots) == 1
        assert snapshots[0].forecast_json == record.forecast_json
        assert snapshots[0].raw_json == record.raw_json


def test_each_save_uses_an_independent_transaction(session_factory):
    hotspot_id = add_hotspot(
        session_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )
    repository = SnapshotRepository(session_factory)
    valid = record_for(hotspot_id)

    with pytest.raises(IntegrityError):
        repository.save_snapshot(replace(valid, congest_level=9))
    repository.save_snapshot(valid)

    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(HotspotSnapshot))
        assert count == 1


def test_repository_appends_parse_failures_with_raw_json(session_factory):
    hotspot_id = add_hotspot(
        session_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )
    raw = {"unexpected": {"secret-shaped-data": "retained-only-as-raw"}}
    failure = ParseFailureRecord(
        hotspot_id=hotspot_id,
        fetched_at=datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        error_type="ValidationError",
        error_message="population response validation failed",
        raw_json=raw,
    )
    repository = SnapshotRepository(session_factory)

    repository.save_parse_failure(failure)
    repository.save_parse_failure(failure)

    with session_factory() as session:
        stored = session.scalars(select(HotspotParseFailure)).all()
        assert len(stored) == 2
        assert stored[0].raw_json == raw
        assert "retained-only-as-raw" not in stored[0].error_message


def test_run_poll_cycle_uses_database_targets_without_real_http(session_factory):
    add_hotspot(
        session_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )
    add_hotspot(
        session_factory,
        area_code="POI089",
        name="호출 제외",
        is_polled=False,
    )
    client = FixtureClient(load_citydata_fixture())

    report = run_poll_cycle(session_factory, client=client)

    assert client.calls == ["광화문광장"]
    assert report.targets == report.saved == 1
    assert report.status == "complete"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(HotspotSnapshot)) == 1
        cycle = session.scalar(select(IngestCycle))
        assert cycle is not None
        assert cycle.status == "complete"
        assert cycle.completed_at is not None


def test_cycle_is_committed_running_before_first_external_call(session_factory):
    add_hotspot(
        session_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )

    class ObservingClient(FixtureClient):
        def fetch_population_raw(self, area_name: str) -> dict[str, Any]:
            with session_factory() as session:
                cycle = session.scalar(select(IngestCycle))
                assert cycle is not None
                assert cycle.status == "running"
                assert cycle.completed_at is None
            return super().fetch_population_raw(area_name)

    report = run_poll_cycle(
        session_factory,
        client=ObservingClient(load_citydata_fixture()),
    )

    assert report.status == "complete"


def test_materialize_exception_marks_cycle_failed_then_reraises(session_factory):
    add_hotspot(
        session_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )

    def fail_materialize(_session: Session) -> None:
        raise RuntimeError("materialize unavailable")

    with pytest.raises(RuntimeError, match="materialize unavailable"):
        run_poll_cycle(
            session_factory,
            client=FixtureClient(load_citydata_fixture()),
            materializer=fail_materialize,
        )

    with session_factory() as session:
        cycle = session.scalar(select(IngestCycle))
        assert cycle is not None
        assert cycle.status == "failed"
        assert cycle.targets == cycle.saved == 1
        assert cycle.failed == 0
        assert cycle.completed_at is not None


def test_keyboard_interrupt_marks_cycle_failed_then_reraises(session_factory):
    add_hotspot(
        session_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )

    class InterruptedClient:
        def fetch_population_raw(self, _area_name: str) -> dict[str, Any]:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_poll_cycle(session_factory, client=InterruptedClient())

    with session_factory() as session:
        cycle = session.scalar(select(IngestCycle))
        assert cycle is not None
        assert cycle.status == "failed"
        assert cycle.saved == 0
        assert cycle.failed == 1
        assert cycle.completed_at is not None


def test_worker_once_honors_database_url_and_uses_injected_client(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'worker.db'}"
    setup_engine = create_engine(database_url)
    Base.metadata.create_all(setup_engine)
    setup_factory = sessionmaker(
        bind=setup_engine, expire_on_commit=False, class_=Session
    )
    add_hotspot(
        setup_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )
    setup_engine.dispose()
    client = FixtureClient(load_citydata_fixture())
    received_keys: list[str] = []

    def client_factory(key: str) -> FixtureClient:
        received_keys.append(key)
        return client

    result = main(
        ["--once", "--database-url", database_url],
        settings_loader=lambda: Settings(
            seoul_api_key=SecretStr("test-key"),
            database_url="sqlite+pysqlite:///ignored.db",
        ),
        client_factory=client_factory,
        engine_factory=lambda url: create_engine(url),
    )

    assert result == 0
    assert received_keys == ["test-key"]
    assert client.calls == ["광화문광장"]
    check_engine = create_engine(database_url)
    with Session(check_engine) as session:
        assert session.scalar(select(func.count()).select_from(HotspotSnapshot)) == 1
        cycle = session.scalar(select(IngestCycle))
        assert cycle is not None
        assert cycle.status == "complete"
    check_engine.dispose()


def test_worker_once_returns_nonzero_for_incomplete_cycle(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'failed-worker.db'}"
    setup_engine = create_engine(database_url)
    Base.metadata.create_all(setup_engine)
    setup_factory = sessionmaker(
        bind=setup_engine, expire_on_commit=False, class_=Session
    )
    add_hotspot(
        setup_factory,
        area_code="POI088",
        name="광화문광장",
        is_polled=True,
    )
    setup_engine.dispose()

    class FailingClient:
        def fetch_population_raw(self, _area_name: str) -> dict[str, Any]:
            raise RuntimeError("upstream unavailable")

    result = main(
        ["--once", "--database-url", database_url],
        settings_loader=lambda: Settings(
            seoul_api_key=SecretStr("test-key"),
            database_url="sqlite+pysqlite:///ignored.db",
        ),
        client_factory=lambda _key: FailingClient(),
        engine_factory=lambda url: create_engine(url),
    )

    assert result == 1
    check_engine = create_engine(database_url)
    with Session(check_engine) as session:
        cycle = session.scalar(select(IngestCycle))
        assert cycle is not None
        assert cycle.status == "failed"
        assert cycle.saved == 0
        assert cycle.failed == 1
    check_engine.dispose()


def test_worker_suppresses_http_client_request_urls() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    suppress_secret_bearing_http_logs()

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
