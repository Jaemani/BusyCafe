"""Backtest saved Seoul forecasts against later actual snapshots, read-only."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import NOWCAST_SHADOW_BACKTEST_WINDOW_DAYS
from app.database import create_db_engine
from app.models import HotspotSnapshot
from app.scoring.nowcast_shadow import (
    NowcastBacktestReport,
    NowcastSnapshot,
    backtest_nowcasts,
)


def load_snapshots(
    session: Session,
    *,
    window_days: int = NOWCAST_SHADOW_BACKTEST_WINDOW_DAYS,
) -> tuple[NowcastSnapshot, ...]:
    if window_days < 1:
        raise ValueError("window_days must be positive")
    latest_observed_at = session.scalar(select(func.max(HotspotSnapshot.observed_at)))
    if latest_observed_at is None:
        return ()
    cutoff = latest_observed_at - timedelta(days=window_days)
    rows = session.execute(
        select(
            HotspotSnapshot.hotspot_id,
            HotspotSnapshot.observed_at,
            HotspotSnapshot.fetched_at,
            HotspotSnapshot.congest_level,
            HotspotSnapshot.ppltn_min,
            HotspotSnapshot.ppltn_max,
            HotspotSnapshot.forecast_json,
        )
        .where(
            HotspotSnapshot.ppltn_min.is_not(None),
            HotspotSnapshot.ppltn_max.is_not(None),
            HotspotSnapshot.observed_at >= cutoff,
        )
        .order_by(
            HotspotSnapshot.hotspot_id,
            HotspotSnapshot.observed_at,
            HotspotSnapshot.fetched_at,
        )
    ).all()
    return tuple(
        NowcastSnapshot(
            hotspot_id=row.hotspot_id,
            observed_at=row.observed_at,
            fetched_at=row.fetched_at,
            level=row.congest_level,
            population_min=row.ppltn_min,
            population_max=row.ppltn_max,
            forecast_json=tuple(row.forecast_json or ()),
        )
        for row in rows
    )


def render_report(report: NowcastBacktestReport) -> str:
    return json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = backtest_nowcasts(load_snapshots(session))
    finally:
        engine.dispose()
    print(render_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
