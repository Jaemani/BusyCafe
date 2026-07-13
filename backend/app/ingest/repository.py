"""SQLAlchemy persistence boundary for a city-data polling cycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ingest.poller import ParseFailureRecord, PollTarget, SnapshotRecord
from app.models import IngestCycle, Hotspot, HotspotParseFailure, HotspotSnapshot


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchPersistenceReport:
    # ``snapshot_saved`` counts successfully handled fetches, including a
    # natural-key duplicate. ``snapshot_inserted`` counts new durable rows.
    # Cycle completeness uses the former; materialization uses the latter.
    snapshot_saved: int
    snapshot_inserted: int
    snapshot_failed: int
    parse_failure_saved: int
    parse_failure_failed: int


class SnapshotRepository:
    """Load active targets and persist deterministic polling results."""

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

    def save_snapshot(self, record: SnapshotRecord) -> bool:
        """Insert one record; return false for a successful duplicate no-op."""

        with self._session_factory() as session:
            if self._snapshot_exists(session, record):
                return False

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
                    return False
                raise
            return True

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

    @staticmethod
    def _snapshot_values(record: SnapshotRecord) -> dict[str, Any]:
        return {
            "hotspot_id": record.hotspot_id,
            "observed_at": record.observed_at,
            "fetched_at": record.fetched_at,
            "congest_level": record.congest_level,
            "congest_label": record.congest_label,
            "ppltn_min": record.ppltn_min,
            "ppltn_max": record.ppltn_max,
            "forecast_json": record.forecast_json,
            "raw_json": record.raw_json,
        }

    @staticmethod
    def _parse_failure_model(
        record: ParseFailureRecord,
    ) -> HotspotParseFailure:
        return HotspotParseFailure(
            hotspot_id=record.hotspot_id,
            fetched_at=record.fetched_at,
            error_type=record.error_type,
            error_message=record.error_message,
            raw_json=record.raw_json,
        )

    def save_batch(
        self,
        snapshots: list[SnapshotRecord],
        parse_failures: list[ParseFailureRecord],
    ) -> BatchPersistenceReport:
        """Persist one ordered poll result in one normal-path transaction.

        Natural-key duplicates remain successful no-ops. If a malformed row
        breaks the batch, fall back to isolated writes so one bad record does
        not discard later valid records.
        """

        if not snapshots and not parse_failures:
            return BatchPersistenceReport(0, 0, 0, 0, 0)

        try:
            with self._session_factory() as session:
                if snapshots:
                    values = [self._snapshot_values(row) for row in snapshots]
                    dialect_name = session.get_bind().dialect.name
                    if dialect_name == "postgresql":
                        statement = postgresql_insert(HotspotSnapshot).values(
                            values
                        )
                    elif dialect_name == "sqlite":
                        statement = sqlite_insert(HotspotSnapshot).values(values)
                    else:  # supported runtimes use PostgreSQL or SQLite
                        raise RuntimeError(
                            f"unsupported snapshot batch dialect: {dialect_name}"
                        )
                    inserted_ids = session.scalars(
                        statement.on_conflict_do_nothing(
                            index_elements=["hotspot_id", "observed_at"]
                        ).returning(HotspotSnapshot.id)
                    ).all()
                else:
                    inserted_ids = []
                if parse_failures:
                    session.add_all(
                        [
                            self._parse_failure_model(row)
                            for row in parse_failures
                        ]
                    )
                session.commit()
            return BatchPersistenceReport(
                snapshot_saved=len(snapshots),
                snapshot_inserted=len(inserted_ids),
                snapshot_failed=0,
                parse_failure_saved=len(parse_failures),
                parse_failure_failed=0,
            )
        except Exception as exc:
            LOGGER.error(
                "Polling batch persistence failed; isolating records (%s)",
                type(exc).__name__,
            )

        snapshot_saved = 0
        snapshot_inserted = 0
        snapshot_failed = 0
        for record in snapshots:
            try:
                inserted = self.save_snapshot(record)
                snapshot_saved += 1
                snapshot_inserted += int(inserted)
            except Exception as exc:
                snapshot_failed += 1
                LOGGER.error(
                    "Hotspot snapshot fallback persistence failed: id=%d (%s)",
                    record.hotspot_id,
                    type(exc).__name__,
                )

        parse_failure_saved = 0
        parse_failure_failed = 0
        for record in parse_failures:
            try:
                self.save_parse_failure(record)
                parse_failure_saved += 1
            except Exception as exc:
                parse_failure_failed += 1
                LOGGER.error(
                    "Hotspot parse-failure fallback persistence failed: "
                    "id=%d (%s)",
                    record.hotspot_id,
                    type(exc).__name__,
                )
        return BatchPersistenceReport(
            snapshot_saved=snapshot_saved,
            snapshot_inserted=snapshot_inserted,
            snapshot_failed=snapshot_failed,
            parse_failure_saved=parse_failure_saved,
            parse_failure_failed=parse_failure_failed,
        )
