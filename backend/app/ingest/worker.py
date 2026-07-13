"""Standalone scheduler process for Seoul city-data ingestion.

Run independently from the API process::

    python -m app.ingest.worker
    python -m app.ingest.worker --once --database-url sqlite:///local.db
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.clients.seoul_citydata import (
    SeoulCityDataClient,
    suppress_secret_bearing_http_logs,
)
from app.config import POLL_INTERVAL_MIN, Settings, get_settings
from app.database import create_db_engine
from app.ingest.poller import (
    ParseFailureRecord,
    PollReport,
    PopulationClient,
    SnapshotRecord,
    poll_once,
)
from app.ingest.repository import SnapshotRepository
from app.scoring.engine import materialize_all


LOGGER = logging.getLogger(__name__)


CycleStatus = Literal["running", "complete", "partial", "failed"]


@dataclass(frozen=True, slots=True)
class CycleReport:
    cycle_id: int
    targets: int
    # Successful target fetches durably handled, including duplicate no-ops.
    saved: int
    # New hotspot snapshot rows; zero means scores are already current.
    inserted: int
    failed: int
    status: CycleStatus
    poll_seconds: float
    fetch_seconds: float
    persistence_seconds: float
    materialize_seconds: float
    finalize_seconds: float
    total_seconds: float


def _log_cycle_phases(
    *,
    status: CycleStatus,
    poll_report: PollReport | None,
    poll_seconds: float,
    materialize_seconds: float,
    finalize_seconds: float,
    total_seconds: float,
) -> None:
    """Log only bounded operational timings, never payloads or request URLs."""

    LOGGER.info(
        "Polling cycle phases: status=%s inserted=%d poll=%.3fs fetch_sum=%.3fs "
        "persist_sum=%.3fs "
        "materialize=%.3fs finalize=%.3fs total=%.3fs",
        status,
        poll_report.inserted if poll_report else 0,
        poll_seconds,
        poll_report.fetch_seconds if poll_report else 0.0,
        poll_report.persistence_seconds if poll_report else 0.0,
        materialize_seconds,
        finalize_seconds,
        total_seconds,
    )


def run_poll_cycle(
    session_factory: sessionmaker[Session],
    *,
    client: PopulationClient,
    materializer: Callable[[Session], Any] = materialize_all,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.perf_counter,
) -> CycleReport:
    cycle_started = monotonic()
    repository = SnapshotRepository(session_factory)
    targets = repository.load_poll_targets()
    cycle_id = repository.start_cycle(targets=len(targets), started_at=clock())
    poll_report: PollReport | None = None
    poll_seconds = 0.0
    materialize_seconds = 0.0
    try:
        snapshots: list[SnapshotRecord] = []
        parse_failures: list[ParseFailureRecord] = []
        poll_started = monotonic()
        try:
            poll_report = poll_once(
                targets,
                client=client,
                # Fetch workers never touch SQLAlchemy. Results are appended
                # and persisted in stable target order on this thread.
                save_snapshot=snapshots.append,
                save_parse_failure=parse_failures.append,
                monotonic=monotonic,
            )
            fetched_report = poll_report
            # Until batch commit returns, none of fetched snapshots are known
            # durable. An interrupt must never publish optimistic saved count.
            poll_report = replace(
                fetched_report,
                saved=0,
                failed=fetched_report.targets,
            )
            persistence_started = monotonic()
            batch_report = repository.save_batch(snapshots, parse_failures)
            poll_report = replace(
                fetched_report,
                saved=batch_report.snapshot_saved,
                inserted=batch_report.snapshot_inserted,
                failed=(
                    fetched_report.failed + batch_report.snapshot_failed
                ),
                persistence_seconds=(
                    fetched_report.persistence_seconds
                    + monotonic()
                    - persistence_started
                ),
            )
        finally:
            poll_seconds = monotonic() - poll_started
        if poll_report.inserted > 0:
            with session_factory() as session:
                materialize_started = monotonic()
                try:
                    materializer(session)
                finally:
                    materialize_seconds = monotonic() - materialize_started
    except (Exception, KeyboardInterrupt, SystemExit):
        # ``poll-production.yml`` gives the worker an explicit SIGINT deadline
        # before the GitHub job timeout.  Python turns SIGINT into
        # KeyboardInterrupt, which must finalize the durable cycle just like a
        # normal exception; otherwise /api/health reports ``running`` forever.
        saved = poll_report.saved if poll_report else 0
        failed = poll_report.failed if poll_report else len(targets)
        finalize_started = monotonic()
        try:
            repository.finish_cycle(
                cycle_id,
                completed_at=clock(),
                saved=saved,
                failed=failed,
                status="failed",
            )
        finally:
            finalize_seconds = monotonic() - finalize_started
            _log_cycle_phases(
                status="failed",
                poll_report=poll_report,
                poll_seconds=poll_seconds,
                materialize_seconds=materialize_seconds,
                finalize_seconds=finalize_seconds,
                total_seconds=monotonic() - cycle_started,
            )
        raise

    is_complete = (
        poll_report.targets > 0
        and poll_report.saved == poll_report.targets
        and poll_report.failed == 0
    )
    status: CycleStatus
    if is_complete:
        status = "complete"
    elif poll_report.saved > 0:
        status = "partial"
    else:
        status = "failed"
    finalize_started = monotonic()
    repository.finish_cycle(
        cycle_id,
        completed_at=clock(),
        saved=poll_report.saved,
        failed=poll_report.failed,
        status=status,
    )
    finalize_seconds = monotonic() - finalize_started
    total_seconds = monotonic() - cycle_started
    _log_cycle_phases(
        status=status,
        poll_report=poll_report,
        poll_seconds=poll_seconds,
        materialize_seconds=materialize_seconds,
        finalize_seconds=finalize_seconds,
        total_seconds=total_seconds,
    )
    return CycleReport(
        cycle_id=cycle_id,
        targets=poll_report.targets,
        saved=poll_report.saved,
        inserted=poll_report.inserted,
        failed=poll_report.failed,
        status=status,
        poll_seconds=poll_seconds,
        fetch_seconds=poll_report.fetch_seconds,
        persistence_seconds=poll_report.persistence_seconds,
        materialize_seconds=materialize_seconds,
        finalize_seconds=finalize_seconds,
        total_seconds=total_seconds,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the city-data poller")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one polling cycle and exit",
    )
    parser.add_argument(
        "--database-url",
        help="override DATABASE_URL for this worker process",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_loader: Callable[[], Settings] = get_settings,
    client_factory: Callable[[str], PopulationClient] = SeoulCityDataClient,
    engine_factory: Callable[[str | None], Engine] = create_db_engine,
    scheduler_factory: Callable[..., Any] = BlockingScheduler,
) -> int:
    args = _parser().parse_args(argv)
    suppress_secret_bearing_http_logs()
    settings = settings_loader()
    if settings.seoul_api_key is None:
        raise SystemExit("SEOUL_API_KEY is required for the ingest worker")

    database_url = args.database_url or settings.database_url
    engine = engine_factory(database_url)
    session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    client = client_factory(settings.seoul_api_key.get_secret_value())

    def cycle() -> CycleReport:
        report = run_poll_cycle(session_factory, client=client)
        LOGGER.info(
            "Polling cycle %s: targets=%d saved=%d inserted=%d failed=%d",
            report.status,
            report.targets,
            report.saved,
            report.inserted,
            report.failed,
        )
        return report

    try:
        if args.once:
            return 0 if cycle().status == "complete" else 1

        scheduler = scheduler_factory(timezone="Asia/Seoul")
        scheduler.add_job(
            cycle,
            "interval",
            minutes=POLL_INTERVAL_MIN,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    finally:
        close_client = getattr(client, "close", None)
        if callable(close_client):
            close_client()
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
