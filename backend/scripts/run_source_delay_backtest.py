"""Run the read-only fixed-horizon Seoul source persistence backtest."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_db_engine
from app.models import HotspotSnapshot
from app.scoring.source_delay_shadow import (
    SourceDelaySnapshot,
    backtest_source_delay,
)


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def load_source_delay_snapshots(session: Session) -> tuple[SourceDelaySnapshot, ...]:
    rows = session.execute(
        select(
            HotspotSnapshot.hotspot_id,
            HotspotSnapshot.observed_at,
            HotspotSnapshot.congest_level,
            HotspotSnapshot.ppltn_min,
            HotspotSnapshot.ppltn_max,
        )
        .where(
            HotspotSnapshot.ppltn_min.is_not(None),
            HotspotSnapshot.ppltn_max.is_not(None),
        )
        .order_by(HotspotSnapshot.hotspot_id, HotspotSnapshot.observed_at)
    ).all()
    return tuple(
        SourceDelaySnapshot(
            hotspot_id=row.hotspot_id,
            observed_at=_utc(row.observed_at),
            level=row.congest_level,
            population_min=row.ppltn_min,
            population_max=row.ppltn_max,
        )
        for row in rows
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = backtest_source_delay(load_source_delay_snapshots(session))
    finally:
        engine.dispose()
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
