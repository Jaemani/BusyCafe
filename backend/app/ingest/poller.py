"""Deterministic polling core for Seoul hotspot population snapshots.

Scheduling and database persistence deliberately live outside this module.  A
caller supplies poll targets, a Seoul client, and a snapshot writer, which
makes one polling cycle deterministic and testable without network or sleep.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.clients.seoul_citydata import SeoulAPIError, parse_population
from app.config import (
    HTTP_MAX_RETRIES,
    HTTP_RETRY_BASE_DELAY_SECONDS,
    POLL_FETCH_CONCURRENCY,
    POLL_MAX_CONSECUTIVE_FAILURES,
)


LOGGER = logging.getLogger(__name__)
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
PPLTN_TIME_FORMAT = "%Y-%m-%d %H:%M"


class PopulationClient(Protocol):
    """Narrow client boundary used by a polling cycle."""

    def fetch_population_raw(self, area_name: str) -> dict[str, Any]: ...


class SnapshotWriter(Protocol):
    """Persistence boundary implemented by the database integration layer."""

    def __call__(self, snapshot: "SnapshotRecord", /) -> None: ...


class ParseFailureWriter(Protocol):
    """Persistence boundary for raw responses that failed validation."""

    def __call__(self, failure: "ParseFailureRecord", /) -> None: ...


@dataclass(frozen=True, slots=True)
class PollTarget:
    hotspot_id: int
    area_cd: str
    area_name: str


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """Persistence-neutral values for one ``hotspot_snapshots`` row."""

    hotspot_id: int
    observed_at: datetime
    fetched_at: datetime
    congest_level: int
    congest_label: str
    ppltn_min: int
    ppltn_max: int
    forecast_json: list[dict[str, Any]]
    raw_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParseFailureRecord:
    """Safe failure metadata plus the separately retained raw response."""

    hotspot_id: int
    fetched_at: datetime
    error_type: str
    error_message: str
    raw_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PollReport:
    targets: int
    # ``saved`` is successful fetch+persistence handling, including duplicate
    # no-ops. It remains the durable ingest-cycle health counter.
    saved: int
    failed: int
    # Filled by the worker after batch persistence; pure polling has no DB.
    inserted: int = 0
    fetch_seconds: float = 0.0
    persistence_seconds: float = 0.0


class PopulationTargetMismatch(ValueError):
    """Raised when a response identifies a different Seoul hotspot."""


def observed_at_to_utc(value: str) -> datetime:
    """Interpret Seoul's timezone-less ``PPLTN_TIME`` and return UTC."""

    local = datetime.strptime(value, PPLTN_TIME_FORMAT).replace(
        tzinfo=SEOUL_TIMEZONE
    )
    return local.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _find_forecast(
    payload: Any, *, area_code: str
) -> list[dict[str, Any]]:
    """Return the raw forecast array belonging to the parsed area record."""

    if isinstance(payload, Mapping):
        if payload.get("AREA_CD") == area_code:
            forecast = payload.get("FCST_PPLTN", [])
            if isinstance(forecast, list) and all(
                isinstance(item, dict) for item in forecast
            ):
                return forecast
        for value in payload.values():
            found = _find_forecast(value, area_code=area_code)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_forecast(value, area_code=area_code)
            if found:
                return found
    return []


def build_snapshot_record(
    target: PollTarget,
    payload: dict[str, Any],
    *,
    fetched_at: datetime,
) -> SnapshotRecord:
    """Validate a measured response and convert it to persistence values."""

    population = parse_population(payload)
    if (
        population.area_code != target.area_cd
        or population.area_name != target.area_name
    ):
        raise PopulationTargetMismatch("population response target mismatch")
    forecast = _find_forecast(payload, area_code=population.area_code)
    return SnapshotRecord(
        hotspot_id=target.hotspot_id,
        observed_at=observed_at_to_utc(population.observed_at),
        fetched_at=_as_utc(fetched_at),
        congest_level=population.numeric_level,
        congest_label=population.congestion_level,
        ppltn_min=population.population_min,
        ppltn_max=population.population_max,
        forecast_json=forecast,
        raw_json=payload,
    )


def poll_once(
    targets: Iterable[PollTarget],
    *,
    client: PopulationClient,
    save_snapshot: SnapshotWriter,
    save_parse_failure: ParseFailureWriter,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.perf_counter,
    max_retries: int = HTTP_MAX_RETRIES,
    retry_base_delay_seconds: float = HTTP_RETRY_BASE_DELAY_SECONDS,
    max_consecutive_failures: int = POLL_MAX_CONSECUTIVE_FAILURES,
    fetch_concurrency: int = POLL_FETCH_CONCURRENCY,
    logger: logging.Logger = LOGGER,
) -> PollReport:
    """Poll every target once, isolating retries and failures per target.

    ``max_retries`` counts retries after the initial request. Backoff delays
    are ``base``, ``base * 2``, ... and can be replaced in tests. Fetches run
    in bounded batches; parsing and persistence stay in deterministic target
    order on the caller thread.
    """

    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if retry_base_delay_seconds < 0:
        raise ValueError("retry_base_delay_seconds must be >= 0")
    if max_consecutive_failures <= 0:
        raise ValueError("max_consecutive_failures must be > 0")
    if fetch_concurrency <= 0:
        raise ValueError("fetch_concurrency must be > 0")

    target_list = tuple(targets)
    saved = 0
    failed = 0
    consecutive_failures = 0
    fetch_seconds = 0.0
    persistence_seconds = 0.0

    def record_target_failure(target_index: int) -> bool:
        """Count one failure and open the outage circuit when bounded."""

        nonlocal consecutive_failures, failed
        failed += 1
        consecutive_failures += 1
        if consecutive_failures < max_consecutive_failures:
            return False

        skipped = len(target_list) - target_index - 1
        failed += skipped
        logger.error(
            "Polling circuit opened after %d consecutive target failures; "
            "skipping %d remaining target(s)",
            consecutive_failures,
            skipped,
        )
        return True

    def persist_snapshot(record: SnapshotRecord) -> None:
        nonlocal persistence_seconds
        started = monotonic()
        try:
            save_snapshot(record)
        finally:
            persistence_seconds += monotonic() - started

    def persist_parse_failure(record: ParseFailureRecord) -> None:
        nonlocal persistence_seconds
        started = monotonic()
        try:
            save_parse_failure(record)
        finally:
            persistence_seconds += monotonic() - started

    def fetch_target(
        target: PollTarget,
    ) -> tuple[dict[str, Any] | None, float]:
        elapsed = 0.0
        for attempt in range(max_retries + 1):
            fetch_started = monotonic()
            try:
                payload = client.fetch_population_raw(target.area_name)
            except SeoulAPIError as exc:
                if attempt == max_retries:
                    logger.error(
                        "Hotspot poll failed after %d attempt(s): %s (%s: %s)",
                        attempt + 1,
                        target.area_name,
                        type(exc).__name__,
                        exc,
                    )
                    elapsed += monotonic() - fetch_started
                    return None, elapsed
                delay = retry_base_delay_seconds * (2**attempt)
                logger.warning(
                    "Hotspot poll attempt %d failed; retrying in %.2fs: "
                    "%s (%s: %s)",
                    attempt + 1,
                    delay,
                    target.area_name,
                    type(exc).__name__,
                    exc,
                )
                sleep(delay)
                elapsed += monotonic() - fetch_started
            except Exception as exc:
                logger.error(
                    "Hotspot fetch failed without retry: %s (%s)",
                    target.area_name,
                    type(exc).__name__,
                )
                elapsed += monotonic() - fetch_started
                return None, elapsed
            else:
                elapsed += monotonic() - fetch_started
                return payload, elapsed
        return None, elapsed

    def process_payload(
        target_index: int,
        target: PollTarget,
        payload: dict[str, Any] | None,
    ) -> bool:
        """Persist one ordered result; return true when circuit opens."""

        nonlocal consecutive_failures, persistence_seconds, saved

        if payload is None:
            return record_target_failure(target_index)

        try:
            fetched_at = _as_utc(clock())
        except Exception as exc:
            logger.error(
                "Hotspot fetch timestamp failed: %s (%s)",
                target.area_name,
                type(exc).__name__,
            )
            if record_target_failure(target_index):
                return True
            return False

        try:
            record = build_snapshot_record(
                target, payload, fetched_at=fetched_at
            )
        except Exception as exc:
            message = (
                "population response target mismatch"
                if isinstance(exc, PopulationTargetMismatch)
                else "population response validation failed"
            )
            failure = ParseFailureRecord(
                hotspot_id=target.hotspot_id,
                fetched_at=fetched_at,
                error_type=type(exc).__name__[:255],
                error_message=message,
                raw_json=payload,
            )
            try:
                persist_parse_failure(failure)
            except Exception as persistence_exc:
                logger.error(
                    "Hotspot parse-failure persistence failed: %s (%s)",
                    target.area_name,
                    type(persistence_exc).__name__,
                )
            logger.warning(
                "Hotspot response parse failed: %s (%s)",
                target.area_name,
                type(exc).__name__,
            )
            if record_target_failure(target_index):
                return True
            return False

        try:
            persist_snapshot(record)
        except Exception as exc:  # persistence failure must not stop later targets
            logger.error(
                "Hotspot snapshot persistence failed: %s (%s)",
                target.area_name,
                type(exc).__name__,
            )
            if record_target_failure(target_index):
                return True
            return False
        saved += 1
        consecutive_failures = 0
        return False

    def process_result(
        target_index: int,
        target: PollTarget,
        result: tuple[dict[str, Any] | None, float],
    ) -> bool:
        nonlocal fetch_seconds
        payload, elapsed = result
        fetch_seconds += elapsed
        return process_payload(target_index, target, payload)

    # Avoid executor overhead for one target and keep injected local clients
    # on their caller thread.
    if len(target_list) == 1:
        process_result(0, target_list[0], fetch_target(target_list[0]))
        return PollReport(
            targets=1,
            saved=saved,
            failed=failed,
            fetch_seconds=fetch_seconds,
            persistence_seconds=persistence_seconds,
        )

    with ThreadPoolExecutor(
        max_workers=fetch_concurrency,
        thread_name_prefix="seoul-poll",
    ) as executor:
        batch_start = 0
        while batch_start < len(target_list):
            # Never speculatively call beyond the remaining circuit budget.
            # A healthy ordered result resets the budget before next batch.
            batch_size = min(
                fetch_concurrency,
                max_consecutive_failures - consecutive_failures,
            )
            batch = target_list[batch_start : batch_start + batch_size]
            futures = [
                executor.submit(fetch_target, target) for target in batch
            ]
            for offset, (target, future) in enumerate(zip(batch, futures)):
                target_index = batch_start + offset
                if process_result(target_index, target, future.result()):
                    for pending in futures[offset + 1 :]:
                        pending.cancel()
                    return PollReport(
                        targets=len(target_list),
                        saved=saved,
                        failed=failed,
                        fetch_seconds=fetch_seconds,
                        persistence_seconds=persistence_seconds,
                    )
            batch_start += len(batch)

    return PollReport(
        targets=len(target_list),
        saved=saved,
        failed=failed,
        fetch_seconds=fetch_seconds,
        persistence_seconds=persistence_seconds,
    )
