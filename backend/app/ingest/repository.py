"""SQLAlchemy persistence boundary for a city-data polling cycle."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ingest.poller import ParseFailureRecord, PollTarget, SnapshotRecord
from app.models import IngestCycle, Hotspot, HotspotParseFailure, HotspotSnapshot


class SnapshotRepository:
    """Load active targets and persist each result in an isolated transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load_poll_targets(self) -> list[PollTarget]:
        with self._session_factory() as session:
            rows = session.execute(
                select(Hotspot.id, Hotspot.area_cd, Hotspot.name)
                .where(Hotspot.is_polled.is_(True))
                .order_by(Hotspot.id)
            ).all()
        return [
            PollTarget(
                hotspot_id=row.id,
                area_cd=row.area_cd,
                area_name=row.name,
            )
            for row in rows
        ]

    def start_cycle(self, *, targets: int, started_at: datetime) -> int:
        """Commit a running cycle before any external polling begins."""

        with self._session_factory() as session:
            cycle = IngestCycle(
                started_at=started_at,
                targets=targets,
                saved=0,
                failed=0,
                status="running",
            )
            session.add(cycle)
            session.commit()
            return cycle.id

    def finish_cycle(
        self,
        cycle_id: int,
        *,
        completed_at: datetime,
        saved: int,
        failed: int,
        status: str,
    ) -> None:
        """Commit final counters and status for a previously started cycle."""

        with self._session_factory() as session:
            cycle = session.get(IngestCycle, cycle_id)
            if cycle is None:
                raise RuntimeError(f"ingest cycle {cycle_id} not found")
            cycle.completed_at = completed_at
            cycle.saved = saved
            cycle.failed = failed
            cycle.status = status
            session.commit()

    @staticmethod
    def _snapshot_exists(session: Session, record: SnapshotRecord) -> bool:
        snapshot_id = session.scalar(
            select(HotspotSnapshot.id).where(
                HotspotSnapshot.hotspot_id == record.hotspot_id,
                HotspotSnapshot.observed_at == record.observed_at,
            )
        )
        return snapshot_id is not None

    def save_snapshot(self, record: SnapshotRecord) -> None:
        """Insert one record, treating its natural-key duplicate as a no-op."""

        with self._session_factory() as session:
            if self._snapshot_exists(session, record):
                return

            session.add(
                HotspotSnapshot(
                    hotspot_id=record.hotspot_id,
                    observed_at=record.observed_at,
                    fetched_at=record.fetched_at,
                    congest_level=record.congest_level,
                    congest_label=record.congest_label,
                    ppltn_min=record.ppltn_min,
                    ppltn_max=record.ppltn_max,
                    forecast_json=record.forecast_json,
                    raw_json=record.raw_json,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                # Another worker may have inserted the same natural key after
                # the preflight query.  Only that race is an idempotent no-op.
                if self._snapshot_exists(session, record):
                    return
                raise

    def save_parse_failure(self, record: ParseFailureRecord) -> None:
        """Append one raw parse failure in its own transaction."""

        with self._session_factory() as session:
            session.add(
                HotspotParseFailure(
                    hotspot_id=record.hotspot_id,
                    fetched_at=record.fetched_at,
                    error_type=record.error_type,
                    error_message=record.error_message,
                    raw_json=record.raw_json,
                )
            )
            session.commit()
