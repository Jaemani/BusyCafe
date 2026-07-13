"""Official Naver Search Local client used only for offline link enrichment."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.config import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_MAX_CONNECTIONS,
    HTTP_MAX_KEEPALIVE_CONNECTIONS,
    HTTP_MAX_RETRIES,
    HTTP_RETRY_BASE_DELAY_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    NAVER_LOCAL_DAILY_CALL_LIMIT,
    NAVER_LOCAL_MAX_DISPLAY,
    NAVER_LOCAL_RETRYABLE_STATUS_CODES,
    NAVER_LOCAL_RETRY_AFTER_MAX_SECONDS,
    NAVER_LOCAL_SEARCH_PATH,
    NAVER_LOCAL_SORT,
    NAVER_SEARCH_BASE_URL,
)
from app.schemas import NaverLocalResponse


class NaverLocalAPIError(RuntimeError):
    """Raised for transport, HTTP, JSON, or response-schema failures."""


def parse_local_search(payload: dict[str, Any]) -> NaverLocalResponse:
    try:
        return NaverLocalResponse.model_validate(payload)
    except ValueError as exc:
        raise NaverLocalAPIError(
            f"invalid Naver local-search response schema ({type(exc).__name__})"
        ) from exc


class NaverLocalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = HTTP_MAX_RETRIES,
        retry_base_delay_seconds: float = HTTP_RETRY_BASE_DELAY_SECONDS,
        request_limit: int = NAVER_LOCAL_DAILY_CALL_LIMIT,
    ) -> None:
        if not client_id.strip() or not client_secret.strip():
            raise ValueError("Naver client credentials must not be empty")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be >= 0")
        if not 1 <= request_limit <= NAVER_LOCAL_DAILY_CALL_LIMIT:
            raise ValueError(
                "request_limit must be between 1 and "
                f"{NAVER_LOCAL_DAILY_CALL_LIMIT}"
            )
        self._sleep = sleep
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._request_limit = request_limit
        self.request_count = 0
        self._client = httpx.Client(
            base_url=NAVER_SEARCH_BASE_URL,
            timeout=httpx.Timeout(
                HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT_SECONDS
            ),
            limits=httpx.Limits(
                max_connections=HTTP_MAX_CONNECTIONS,
                max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
            ),
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "User-Agent": HTTP_USER_AGENT,
            },
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NaverLocalClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _retry_delay(self, response: httpx.Response | None, index: int) -> float:
        if response is not None and response.status_code == 429:
            raw = response.headers.get("Retry-After")
            if raw is not None:
                try:
                    return min(
                        max(0.0, float(raw)), NAVER_LOCAL_RETRY_AFTER_MAX_SECONDS
                    )
                except ValueError:
                    pass
        return self._retry_base_delay_seconds * (2**index)

    def search_local_raw(
        self,
        query: str,
        *,
        display: int = NAVER_LOCAL_MAX_DISPLAY,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if not 1 <= display <= NAVER_LOCAL_MAX_DISPLAY:
            raise ValueError(
                f"display must be between 1 and {NAVER_LOCAL_MAX_DISPLAY}"
            )
        params: dict[str, str | int] = {
            "query": normalized_query,
            "display": display,
            "start": 1,
            "sort": NAVER_LOCAL_SORT,
        }
        for retry_index in range(self._max_retries + 1):
            response: httpx.Response | None = None
            try:
                if self.request_count >= self._request_limit:
                    raise NaverLocalAPIError(
                        "Naver local-search request limit reached"
                    )
                self.request_count += 1
                response = self._client.get(NAVER_LOCAL_SEARCH_PATH, params=params)
                if response.status_code in NAVER_LOCAL_RETRYABLE_STATUS_CODES:
                    if retry_index < self._max_retries:
                        self._sleep(self._retry_delay(response, retry_index))
                        continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise NaverLocalAPIError(
                        "Naver local-search response root must be an object"
                    )
                return payload
            except NaverLocalAPIError:
                raise
            except httpx.HTTPStatusError as exc:
                raise NaverLocalAPIError(
                    f"Naver local-search API returned HTTP {exc.response.status_code}"
                ) from None
            except httpx.HTTPError as exc:
                if retry_index < self._max_retries:
                    self._sleep(self._retry_delay(response, retry_index))
                    continue
                raise NaverLocalAPIError(
                    f"Naver local-search request failed ({type(exc).__name__})"
                ) from None
            except ValueError:
                raise NaverLocalAPIError(
                    "Naver local-search API returned invalid JSON"
                ) from None
        raise NaverLocalAPIError("Naver local-search request exhausted retries")

    def search_local(
        self,
        query: str,
        *,
        display: int = NAVER_LOCAL_MAX_DISPLAY,
    ) -> NaverLocalResponse:
        return parse_local_search(self.search_local_raw(query, display=display))
