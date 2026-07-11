from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from scripts.check_freshness import (
    FreshnessCheckError,
    check_freshness,
    evaluate_health,
    fetch_health,
)


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.status = status
        self.body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def opener_for(payload: object, status: int = 200):
    def opener(request: object, *, timeout: float) -> FakeResponse:
        assert request is not None
        assert timeout > 0
        return FakeResponse(payload, status)

    return opener


def health_payload(
    *,
    completed_at: str = "2026-07-12T20:40:00+09:00",
    status: str = "complete",
) -> dict[str, str]:
    return {
        "last_complete_cycle_at": completed_at,
        "last_cycle_status": status,
    }


def test_check_freshness_accepts_timezone_aware_recent_complete_cycle() -> None:
    result = check_freshness(
        "https://example.test/api/health",
        now=NOW,
        opener=opener_for(health_payload()),
    )

    assert result.last_complete_cycle_at == datetime(2026, 7, 12, 11, 40, tzinfo=UTC)
    assert result.last_cycle_status == "complete"
    assert result.age_minutes == 20


def test_evaluate_health_allows_running_cycle_with_fresh_prior_completion() -> None:
    result = evaluate_health(health_payload(status="running"), now=NOW)

    assert result.last_cycle_status == "running"
    assert result.age_minutes == 20


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_evaluate_health_rejects_unsuccessful_latest_cycle(status: str) -> None:
    with pytest.raises(FreshnessCheckError, match=f"cycle is {status}"):
        evaluate_health(health_payload(status=status), now=NOW)


def test_evaluate_health_rejects_stale_complete_cycle() -> None:
    with pytest.raises(FreshnessCheckError, match=r"26.0 minutes old.*limit 25"):
        evaluate_health(
            health_payload(completed_at="2026-07-12T11:34:00Z"),
            now=NOW,
        )


def test_evaluate_health_rejects_complete_cycle_too_far_in_future() -> None:
    with pytest.raises(FreshnessCheckError, match=r"3.0 minutes ahead.*limit 2"):
        evaluate_health(
            health_payload(completed_at="2026-07-12T12:03:00Z"),
            now=NOW,
        )


def test_evaluate_health_allows_small_clock_skew() -> None:
    result = evaluate_health(
        health_payload(completed_at="2026-07-12T12:02:00Z"),
        now=NOW,
    )

    assert result.age_minutes == -2


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "missing or invalid last_cycle_status"),
        (
            {"last_cycle_status": "complete"},
            "missing last_complete_cycle_at",
        ),
        (
            health_payload(completed_at="2026-07-12T11:40:00"),
            "must include a timezone",
        ),
        (
            health_payload(completed_at="not-a-date"),
            "not a valid ISO 8601",
        ),
        (
            {
                "last_complete_cycle_at": "2026-07-12T11:40:00Z",
                "last_cycle_status": "unknown",
            },
            "missing or invalid last_cycle_status",
        ),
        ([], "must be a JSON object"),
    ],
)
def test_evaluate_health_rejects_invalid_payload(payload: object, message: str) -> None:
    with pytest.raises(FreshnessCheckError, match=message):
        evaluate_health(payload, now=NOW)


def test_fetch_health_rejects_non_200_response() -> None:
    with pytest.raises(FreshnessCheckError, match="HTTP 503"):
        fetch_health(
            "https://example.test/api/health",
            opener=opener_for({"detail": "unavailable"}, status=503),
        )


def test_fetch_health_normalizes_urllib_http_error() -> None:
    def failing_opener(request: object, *, timeout: float) -> FakeResponse:
        raise HTTPError(
            "https://example.test/api/health", 502, "Bad Gateway", {}, None
        )

    with pytest.raises(FreshnessCheckError, match="HTTP 502"):
        fetch_health("https://example.test/api/health", opener=failing_opener)


@pytest.mark.parametrize("url", ["http://example.test/api/health", "file:///tmp/health.json"])
def test_fetch_health_rejects_non_https_url_without_calling_opener(url: str) -> None:
    called = False

    def opener(request: object, *, timeout: float) -> FakeResponse:
        nonlocal called
        called = True
        return FakeResponse({})

    with pytest.raises(FreshnessCheckError, match="absolute HTTPS URL"):
        fetch_health(url, opener=opener)

    assert called is False


def test_fetch_health_preserves_query_and_adds_cache_buster() -> None:
    captured_url = ""

    def opener(request: object, *, timeout: float) -> FakeResponse:
        nonlocal captured_url
        captured_url = request.full_url  # type: ignore[attr-defined]
        return FakeResponse(health_payload())

    fetch_health(
        "https://example.test/api/health?deployment=production&empty=",
        opener=opener,
    )

    query = parse_qs(urlparse(captured_url).query, keep_blank_values=True)
    assert query["deployment"] == ["production"]
    assert query["empty"] == [""]
    assert len(query["_freshness_check"]) == 1
