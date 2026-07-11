"""Standalone scheduler process for Seoul city-data ingestion.

Run independently from the API process::

    python -m app.ingest.worker
    python -m app.ingest.worker --once --database-url sqlite:///local.db
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from typing import Any

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


def run_poll_cycle(
    session_factory: sessionmaker[Session],
    *,
    client: PopulationClient,
) -> PollReport:
    repository = SnapshotRepository(session_factory)
    report = poll_once(
        repository.load_poll_targets(),
        client=client,
        save_snapshot=repository.save_snapshot,
        save_parse_failure=repository.save_parse_failure,
    )
    with session_factory() as session:
        materialize_all(session)
    return report


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

    def cycle() -> PollReport:
        report = run_poll_cycle(session_factory, client=client)
        LOGGER.info(
            "Polling cycle complete: targets=%d saved=%d failed=%d",
            report.targets,
            report.saved,
            report.failed,
        )
        return report

    try:
        if args.once:
            cycle()
            return 0

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
