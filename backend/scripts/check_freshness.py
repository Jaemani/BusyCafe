"""Fail when the production health endpoint reports stale ingest data."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from app.config import (
    FRESHNESS_MAX_FUTURE_SKEW_MIN,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    STALE_WARN_MIN,
)


class Response(Protocol):
    """Small subset of urllib responses needed by the checker."""

    status: int

    def read(self) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *args: object) -> None: ...


OpenUrl = Callable[..., Response]


class FreshnessCheckError(RuntimeError):
    """Raised when health cannot prove that production data is fresh."""


@dataclass(frozen=True)
class FreshnessResult:
    last_complete_cycle_at: datetime
    last_cycle_status: str
    checked_at: datetime
    age_minutes: float


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FreshnessCheckError(f"health response is missing {field}")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreshnessCheckError(
            f"{field} is not a valid ISO 8601 timestamp"
        ) from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise FreshnessCheckError(f"{field} must include a timezone")
    return timestamp.astimezone(UTC)


def evaluate_health(
    payload: Any,
    *,
    now: datetime,
    max_age_minutes: int = STALE_WARN_MIN,
    max_future_skew_minutes: int = FRESHNESS_MAX_FUTURE_SKEW_MIN,
) -> FreshnessResult:
    """Validate health JSON and calculate the latest complete-cycle age."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if max_age_minutes < 0:
        raise ValueError("max_age_minutes must be non-negative")
    if max_future_skew_minutes < 0:
        raise ValueError("max_future_skew_minutes must be non-negative")
    if not isinstance(payload, dict):
        raise FreshnessCheckError("health response must be a JSON object")

    status = payload.get("last_cycle_status")
    if status not in {"running", "complete", "partial", "failed"}:
        raise FreshnessCheckError(
            "health response has missing or invalid last_cycle_status"
        )
    if status in {"partial", "failed"}:
        raise FreshnessCheckError(f"latest production ingest cycle is {status}")

    checked_at = now.astimezone(UTC)
    last_complete_cycle_at = _parse_timestamp(
        payload.get("last_complete_cycle_at"), field="last_complete_cycle_at"
    )
    age_minutes = (checked_at - last_complete_cycle_at).total_seconds() / 60
    if age_minutes < -max_future_skew_minutes:
        raise FreshnessCheckError(
            "last_complete_cycle_at is too far in the future: "
            f"{-age_minutes:.1f} minutes ahead "
            f"(limit {max_future_skew_minutes} minutes)"
        )
    if age_minutes > max_age_minutes:
        raise FreshnessCheckError(
            f"last complete production ingest cycle is stale: {age_minutes:.1f} minutes old "
            f"(limit {max_age_minutes} minutes)"
        )
    return FreshnessResult(
        last_complete_cycle_at=last_complete_cycle_at,
        last_cycle_status=status,
        checked_at=checked_at,
        age_minutes=age_minutes,
    )


def _cache_busted_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_freshness_check", str(time.time_ns())))
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch_health(
    url: str,
    *,
    opener: OpenUrl = urlopen,
    timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
) -> Any:
    """Fetch and decode health JSON using an injectable standard-library opener."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise FreshnessCheckError("production health URL must be an absolute HTTPS URL")

    request = Request(
        _cache_busted_url(url),
        headers={"User-Agent": HTTP_USER_AGENT, "Cache-Control": "no-cache"},
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise FreshnessCheckError(
                    f"health endpoint returned HTTP {response.status}"
                )
            body = response.read()
    except FreshnessCheckError:
        raise
    except HTTPError as exc:
        raise FreshnessCheckError(f"health endpoint returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise FreshnessCheckError(f"health request failed: {exc}") from exc

    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreshnessCheckError("health endpoint did not return valid JSON") from exc


def check_freshness(
    url: str,
    *,
    now: datetime | None = None,
    max_age_minutes: int = STALE_WARN_MIN,
    opener: OpenUrl = urlopen,
) -> FreshnessResult:
    payload = fetch_health(url, opener=opener)
    return evaluate_health(
        payload,
        now=now or datetime.now(UTC),
        max_age_minutes=max_age_minutes,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Absolute URL of the production /api/health endpoint")
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=STALE_WARN_MIN,
        help=f"Maximum accepted ingest age (default: {STALE_WARN_MIN})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = check_freshness(args.url, max_age_minutes=args.max_age_minutes)
    except (FreshnessCheckError, ValueError) as exc:
        print(f"freshness check failed: {exc}", file=sys.stderr)
        return 1

    print(
        "last complete production ingest cycle fresh: "
        f"age={result.age_minutes:.1f}m "
        f"last_complete_cycle_at={result.last_complete_cycle_at.isoformat()} "
        f"last_cycle_status={result.last_cycle_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
