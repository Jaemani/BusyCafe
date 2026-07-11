"""Deterministic polling core for Seoul hotspot population snapshots.

Scheduling and database persistence deliberately live outside this module.  A
caller supplies poll targets, a Seoul client, and a snapshot writer, which
makes one polling cycle deterministic and testable without network or sleep.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.clients.seoul_citydata import SeoulAPIError, parse_population
from app.config import HTTP_MAX_RETRIES, HTTP_RETRY_BASE_DELAY_SECONDS


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
    saved: int
    failed: int


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
    max_retries: int = HTTP_MAX_RETRIES,
    retry_base_delay_seconds: float = HTTP_RETRY_BASE_DELAY_SECONDS,
    logger: logging.Logger = LOGGER,
) -> PollReport:
    """Poll every target once, isolating retries and failures per target.

    ``max_retries`` counts retries after the initial request.  Backoff delays
    are ``base``, ``base * 2``, ... and can be replaced in tests.
    """

    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if retry_base_delay_seconds < 0:
        raise ValueError("retry_base_delay_seconds must be >= 0")

    target_list = tuple(targets)
    saved = 0
    failed = 0

    for target in target_list:
        payload: dict[str, Any] | None = None
        for attempt in range(max_retries + 1):
            try:
                payload = client.fetch_population_raw(target.area_name)
                break
            except SeoulAPIError as exc:
                if attempt == max_retries:
                    logger.error(
                        "Hotspot poll failed after %d attempt(s): %s (%s)",
                        attempt + 1,
                        target.area_name,
                        type(exc).__name__,
                    )
                    break
                delay = retry_base_delay_seconds * (2**attempt)
                logger.warning(
                    "Hotspot poll attempt %d failed; retrying in %.2fs: %s (%s)",
                    attempt + 1,
                    delay,
                    target.area_name,
                    type(exc).__name__,
                )
                sleep(delay)
            except Exception as exc:
                logger.error(
                    "Hotspot fetch failed without retry: %s (%s)",
                    target.area_name,
                    type(exc).__name__,
                )
                break

        if payload is None:
            failed += 1
            continue

        try:
            fetched_at = _as_utc(clock())
        except Exception as exc:
            logger.error(
                "Hotspot fetch timestamp failed: %s (%s)",
                target.area_name,
                type(exc).__name__,
            )
            failed += 1
            continue

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
                save_parse_failure(failure)
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
            failed += 1
            continue

        try:
            save_snapshot(record)
        except Exception as exc:  # persistence failure must not stop later targets
            logger.error(
                "Hotspot snapshot persistence failed: %s (%s)",
                target.area_name,
                type(exc).__name__,
            )
            failed += 1
            continue
        saved += 1

    return PollReport(targets=len(target_list), saved=saved, failed=failed)
