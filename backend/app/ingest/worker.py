"""Standalone scheduler process for Seoul city-data ingestion.

Run independently from the API process::

    python -m app.ingest.worker
    python -m app.ingest.worker --once --database-url sqlite:///local.db
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
from app.ingest.poller import PollReport, PopulationClient, poll_once
from app.ingest.repository import SnapshotRepository
from app.scoring.engine import materialize_all


LOGGER = logging.getLogger(__name__)


CycleStatus = Literal["running", "complete", "partial", "failed"]


@dataclass(frozen=True, slots=True)
class CycleReport:
    cycle_id: int
    targets: int
    saved: int
    failed: int
    status: CycleStatus


def run_poll_cycle(
    session_factory: sessionmaker[Session],
    *,
    client: PopulationClient,
    materializer: Callable[[Session], Any] = materialize_all,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CycleReport:
    repository = SnapshotRepository(session_factory)
    targets = repository.load_poll_targets()
    cycle_id = repository.start_cycle(targets=len(targets), started_at=clock())
    poll_report: PollReport | None = None
    try:
        poll_report = poll_once(
            targets,
            client=client,
            save_snapshot=repository.save_snapshot,
            save_parse_failure=repository.save_parse_failure,
        )
        with session_factory() as session:
            materializer(session)
    except (Exception, KeyboardInterrupt, SystemExit):
        # ``poll-production.yml`` gives the worker an explicit SIGINT deadline
        # before the GitHub job timeout.  Python turns SIGINT into
        # KeyboardInterrupt, which must finalize the durable cycle just like a
        # normal exception; otherwise /api/health reports ``running`` forever.
        saved = poll_report.saved if poll_report else 0
        failed = poll_report.failed if poll_report else len(targets)
        repository.finish_cycle(
            cycle_id,
            completed_at=clock(),
            saved=saved,
            failed=failed,
            status="failed",
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
    repository.finish_cycle(
        cycle_id,
        completed_at=clock(),
        saved=poll_report.saved,
        failed=poll_report.failed,
        status=status,
    )
    return CycleReport(
        cycle_id=cycle_id,
        targets=poll_report.targets,
        saved=poll_report.saved,
        failed=poll_report.failed,
        status=status,
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
            "Polling cycle %s: targets=%d saved=%d failed=%d",
            report.status,
            report.targets,
            report.saved,
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
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
