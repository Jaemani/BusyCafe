"""Client for Kakao Local API category search."""

from __future__ import annotations

import time
from collections.abc import Callable
from math import isfinite
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
    KAKAO_CAFE_CATEGORY_CODE,
    KAKAO_CATEGORY_PATH,
    KAKAO_LOCAL_BASE_URL,
    KAKAO_MAX_PAGES,
    KAKAO_MAX_RADIUS_M,
    KAKAO_PAGE_SIZE,
    KAKAO_RETRYABLE_STATUS_CODES,
    KAKAO_RETRY_AFTER_MAX_SECONDS,
)
from app.schemas import KakaoCategoryResponse


class KakaoAPIError(RuntimeError):
    """Raised when Kakao returns an invalid response."""


class KakaoLocalClient:
    def __init__(
        self,
        rest_api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = HTTP_MAX_RETRIES,
        retry_base_delay_seconds: float = HTTP_RETRY_BASE_DELAY_SECONDS,
    ) -> None:
        if not rest_api_key.strip():
            raise ValueError("rest_api_key must not be empty")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be >= 0")
        self._rest_api_key = rest_api_key
        self._sleep = sleep
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self.request_count = 0
        self._client = httpx.Client(
            base_url=KAKAO_LOCAL_BASE_URL,
            timeout=httpx.Timeout(
                HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT_SECONDS
            ),
            limits=httpx.Limits(
                max_connections=HTTP_MAX_CONNECTIONS,
                max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
            ),
            headers={
                "Authorization": f"KakaoAK {self._rest_api_key}",
                "User-Agent": HTTP_USER_AGENT,
            },
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KakaoLocalClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _retry_delay(self, response: httpx.Response | None, retry_index: int) -> float:
        if response is not None and response.status_code == 429:
            raw_retry_after = response.headers.get("Retry-After")
            if raw_retry_after is not None:
                try:
                    return min(
                        max(0.0, float(raw_retry_after)),
                        KAKAO_RETRY_AFTER_MAX_SECONDS,
                    )
                except ValueError:
                    pass
        return self._retry_base_delay_seconds * (2**retry_index)

    def search_category_raw(
        self,
        *,
        longitude: float | None = None,
        latitude: float | None = None,
        radius_m: int | None = None,
        page: int = 1,
        size: int = KAKAO_PAGE_SIZE,
        category_group_code: str = KAKAO_CAFE_CATEGORY_CODE,
        rect: tuple[float, float, float, float] | None = None,
    ) -> dict[str, Any]:
        if rect is None:
            if longitude is None or latitude is None or radius_m is None:
                raise ValueError("longitude, latitude, and radius_m are required")
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError("longitude/latitude are outside valid ranges")
            if not 1 <= radius_m <= KAKAO_MAX_RADIUS_M:
                raise ValueError(
                    f"radius_m must be between 1 and {KAKAO_MAX_RADIUS_M}"
                )
        else:
            if len(rect) != 4 or not all(isfinite(value) for value in rect):
                raise ValueError("rect must contain four finite coordinates")
            min_lng, min_lat, max_lng, max_lat = rect
            if not (
                -180 <= min_lng < max_lng <= 180
                and -90 <= min_lat < max_lat <= 90
            ):
                raise ValueError("rect coordinates are invalid")
        if not 1 <= page <= KAKAO_MAX_PAGES:
            raise ValueError(f"page must be between 1 and {KAKAO_MAX_PAGES}")
        if not 1 <= size <= KAKAO_PAGE_SIZE:
            raise ValueError(f"size must be between 1 and {KAKAO_PAGE_SIZE}")

        params: dict[str, str | float | int] = {
            "category_group_code": category_group_code,
            "page": page,
            "size": size,
        }
        if rect is None:
            params.update({"x": longitude, "y": latitude, "radius": radius_m})
        else:
            params["rect"] = ",".join(format(value, ".12g") for value in rect)

        payload: Any = None
        response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                self.request_count += 1
                response = self._client.get(KAKAO_CATEGORY_PATH, params=params)
                if (
                    response.status_code in KAKAO_RETRYABLE_STATUS_CODES
                    and attempt < self._max_retries
                ):
                    self._sleep(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                raise KakaoAPIError(
                    f"Kakao API returned HTTP {exc.response.status_code}"
                ) from None
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    self._sleep(self._retry_delay(None, attempt))
                    continue
                raise KakaoAPIError(
                    f"Kakao request failed ({type(exc).__name__})"
                ) from None
            except httpx.HTTPError as exc:
                raise KakaoAPIError(
                    f"Kakao request failed ({type(exc).__name__})"
                ) from None
            except ValueError:
                raise KakaoAPIError("Kakao API returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise KakaoAPIError("Kakao API response root must be a JSON object")
        return payload

    def search_category(self, **kwargs: Any) -> KakaoCategoryResponse:
        return KakaoCategoryResponse.model_validate(self.search_category_raw(**kwargs))


def parse_category(payload: dict[str, Any]) -> KakaoCategoryResponse:
    """Parse a stored fixture without making a network call."""

    return KakaoCategoryResponse.model_validate(payload)
