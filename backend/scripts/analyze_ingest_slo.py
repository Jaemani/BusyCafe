"""Emit a deterministic read-only JSON report for production ingest SLOs."""

from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import ceil, floor, isfinite
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import (
    INGEST_SLO_CADENCE_GAP_FACTOR,
    INGEST_SLO_DEFAULT_WINDOW_HOURS,
    POLL_INTERVAL_MIN,
)
from app.database import create_db_engine
from app.models import Hotspot, HotspotSnapshot, IngestCycle


_CYCLE_STATUSES = ("complete", "partial", "failed", "running")


class ReadOnlySession(Protocol):
    def get_bind(self): ...

    def execute(self, statement): ...


def enforce_transaction_read_only(session: ReadOnlySession) -> None:
    """Make PostgreSQL reject every write in this report transaction."""

    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite drops timezone metadata; all persisted application timestamps
        # use UTC by contract.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    """R7 linear interpolation, matching common analytical tools."""

    if not values:
        return None
    if not 0 <= percentile <= 1:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = floor(position)
    upper = ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _parse_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--as-of must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone")
    return parsed.astimezone(UTC)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a number") from error
    if not isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def analyze_ingest_slo(
    session: Session,
    *,
    as_of: datetime,
    window_hours: float = INGEST_SLO_DEFAULT_WINDOW_HOURS,
    expected_cadence_min: float = POLL_INTERVAL_MIN,
    cadence_gap_factor: float = INGEST_SLO_CADENCE_GAP_FACTOR,
) -> dict[str, Any]:
    """Analyze cached operational records without mutating database state."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    checked_at = _utc(as_of, field="as_of")
    if not isfinite(window_hours) or window_hours <= 0:
        raise ValueError("window_hours must be positive")
    if not isfinite(expected_cadence_min) or expected_cadence_min <= 0:
        raise ValueError("expected_cadence_min must be positive")
    if not isfinite(cadence_gap_factor) or cadence_gap_factor < 1:
        raise ValueError("cadence_gap_factor must be at least one")
    window_start = checked_at - timedelta(hours=window_hours)
    enforce_transaction_read_only(session)

    cycle_rows = session.execute(
        select(
            IngestCycle.id,
            IngestCycle.started_at,
            IngestCycle.completed_at,
            IngestCycle.targets,
            IngestCycle.saved,
            IngestCycle.failed,
            IngestCycle.status,
        )
        .where(
            IngestCycle.started_at >= window_start,
            IngestCycle.started_at <= checked_at,
        )
        .order_by(IngestCycle.started_at, IngestCycle.id)
    ).all()
    cycles = [
        {
            "id": row.id,
            "started_at": _utc(row.started_at, field="started_at"),
            "completed_at": (
                _utc(row.completed_at, field="completed_at")
                if row.completed_at is not None
                else None
            ),
            "targets": row.targets,
            "saved": row.saved,
            "failed": row.failed,
            "status": row.status,
        }
        for row in cycle_rows
    ]

    hotspot_rows = session.execute(
        select(Hotspot.id, Hotspot.area_cd, Hotspot.name)
        .where(Hotspot.is_polled.is_(True))
        .order_by(Hotspot.id)
    ).all()
    polled_hotspots = {
        row.id: {"id": row.id, "area_cd": row.area_cd, "name": row.name}
        for row in hotspot_rows
    }
    snapshot_rows = session.execute(
        select(
            HotspotSnapshot.hotspot_id,
            HotspotSnapshot.observed_at,
            HotspotSnapshot.fetched_at,
        )
        .join(Hotspot, Hotspot.id == HotspotSnapshot.hotspot_id)
        .where(
            Hotspot.is_polled.is_(True),
            HotspotSnapshot.fetched_at >= window_start,
            HotspotSnapshot.fetched_at <= checked_at,
        )
        .order_by(
            HotspotSnapshot.fetched_at,
            HotspotSnapshot.hotspot_id,
            HotspotSnapshot.observed_at,
        )
    ).all()
    snapshots = [
        {
            "hotspot_id": row.hotspot_id,
            "observed_at": _utc(row.observed_at, field="observed_at"),
            "fetched_at": _utc(row.fetched_at, field="fetched_at"),
        }
        for row in snapshot_rows
    ]

    status_counts = Counter(cycle["status"] for cycle in cycles)
    terminal = [cycle for cycle in cycles if cycle["status"] != "running"]
    target_total = sum(cycle["targets"] for cycle in terminal)
    saved_total = sum(cycle["saved"] for cycle in terminal)
    durations: list[float] = []
    invalid_durations = 0
    for cycle in terminal:
        completed_at = cycle["completed_at"]
        if completed_at is None:
            invalid_durations += 1
            continue
        duration = (completed_at - cycle["started_at"]).total_seconds()
        if duration < 0:
            invalid_durations += 1
            continue
        durations.append(duration)

    cycle_starts = [cycle["started_at"] for cycle in cycles]
    cadence_intervals_min = [
        (current - previous).total_seconds() / 60.0
        for previous, current in zip(cycle_starts, cycle_starts[1:])
    ]
    gap_threshold_min = expected_cadence_min * cadence_gap_factor
    cadence_gaps: list[dict[str, Any]] = []

    def record_gap(kind: str, start: datetime, end: datetime) -> None:
        gap_min = max(0.0, (end - start).total_seconds() / 60.0)
        if gap_min < gap_threshold_min:
            return
        cadence_gaps.append(
            {
                "kind": kind,
                "from": _iso(start),
                "to": _iso(end),
                "gap_min": gap_min,
                "estimated_missed_cycles": max(
                    0, ceil(gap_min / expected_cadence_min) - 1
                ),
            }
        )

    if cycles:
        record_gap("window_head", window_start, cycle_starts[0])
        for previous, current in zip(cycle_starts, cycle_starts[1:]):
            record_gap("between_cycles", previous, current)
        record_gap("window_tail", cycle_starts[-1], checked_at)
    else:
        record_gap("empty_window", window_start, checked_at)

    lag_minutes: list[float] = []
    negative_lag_count = 0
    snapshots_per_hotspot: Counter[int] = Counter()
    for snapshot in snapshots:
        snapshots_per_hotspot[snapshot["hotspot_id"]] += 1
        lag = (
            snapshot["fetched_at"] - snapshot["observed_at"]
        ).total_seconds() / 60.0
        if lag < 0:
            negative_lag_count += 1
        else:
            lag_minutes.append(lag)

    # Derive new/duplicate signal because the current ingest_cycles schema
    # persists target success but not the worker's inserted counter.
    cycle_inserted_counts: Counter[int] = Counter()
    if cycles:
        for snapshot in snapshots:
            index = bisect_right(cycle_starts, snapshot["fetched_at"]) - 1
            while index >= 0:
                cycle = cycles[index]
                cycle_end = cycle["completed_at"] or checked_at
                if snapshot["fetched_at"] <= cycle_end:
                    cycle_inserted_counts[cycle["id"]] += 1
                    break
                index -= 1
    cycle_signals: list[dict[str, Any]] = []
    for cycle in cycles:
        new_count = cycle_inserted_counts[cycle["id"]]
        if new_count > 0:
            signal = "new_data"
        elif cycle["status"] == "complete":
            signal = "duplicate_or_unchanged"
        elif cycle["status"] == "running":
            signal = "pending"
        else:
            signal = "no_new_data_failure"
        cycle_signals.append(
            {
                "cycle_id": cycle["id"],
                "status": cycle["status"],
                "new_snapshot_count": new_count,
                "signal": signal,
            }
        )
    signal_counts = Counter(item["signal"] for item in cycle_signals)

    seen_hotspots = set(snapshots_per_hotspot)
    missing_hotspots = [
        polled_hotspots[hotspot_id]
        for hotspot_id in sorted(set(polled_hotspots) - seen_hotspots)
    ]
    per_hotspot_counts = [
        float(snapshots_per_hotspot[hotspot_id])
        for hotspot_id in sorted(seen_hotspots)
    ]
    terminal_count = len(terminal)
    expected_cycle_slots = floor(
        window_hours * 60.0 / expected_cadence_min
    )
    return {
        "schema_version": 1,
        "generated_at": _iso(checked_at),
        "window": {
            "start": _iso(window_start),
            "end": _iso(checked_at),
            "hours": window_hours,
        },
        "cycles": {
            "total": len(cycles),
            "terminal": terminal_count,
            "status_counts": {
                status: status_counts[status] for status in _CYCLE_STATUSES
            },
            "complete_rate": (
                status_counts["complete"] / terminal_count
                if terminal_count
                else None
            ),
            "target_success_rate": (
                saved_total / target_total if target_total else None
            ),
            "duration_seconds": {
                **_distribution(durations),
                "invalid_or_missing": invalid_durations,
            },
        },
        "cadence": {
            "expected_interval_min": expected_cadence_min,
            "expected_cycle_slots": expected_cycle_slots,
            "observed_cycle_starts": len(cycles),
            "gap_threshold_min": gap_threshold_min,
            "interval_minutes": _distribution(cadence_intervals_min),
            "gap_count": len(cadence_gaps),
            "estimated_missed_cycles": sum(
                item["estimated_missed_cycles"] for item in cadence_gaps
            ),
            "gaps": cadence_gaps,
        },
        "snapshot_observed_lag_minutes": {
            **_distribution(lag_minutes),
            "negative_lag_count": negative_lag_count,
            "percentile_method": "linear_interpolation_r7",
        },
        "cycle_snapshot_signal": {
            "supported": True,
            "direct_persisted_insert_counter": False,
            "method": "snapshot_fetched_at_within_cycle_window",
            "signal_counts": dict(sorted(signal_counts.items())),
            "cycles": cycle_signals,
        },
        "hotspot_coverage": {
            "polled_hotspots": len(polled_hotspots),
            "hotspots_seen_in_window": len(seen_hotspots),
            "coverage_rate": (
                len(seen_hotspots) / len(polled_hotspots)
                if polled_hotspots
                else None
            ),
            "snapshot_count_per_seen_hotspot": _distribution(
                per_hotspot_counts
            ),
            "missing_hotspots": missing_hotspots,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument(
        "--window-hours",
        type=_positive_float,
        default=INGEST_SLO_DEFAULT_WINDOW_HOURS,
    )
    parser.add_argument("--as-of", type=_parse_as_of)
    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = analyze_ingest_slo(
                session,
                as_of=args.as_of or datetime.now(UTC),
                window_hours=args.window_hours,
            )
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
