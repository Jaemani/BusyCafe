"""Client for Kakao Local API category search."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    KAKAO_CAFE_CATEGORY_CODE,
    KAKAO_CATEGORY_PATH,
    KAKAO_LOCAL_BASE_URL,
    KAKAO_MAX_PAGES,
    KAKAO_MAX_RADIUS_M,
    KAKAO_PAGE_SIZE,
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
    ) -> None:
        if not rest_api_key.strip():
            raise ValueError("rest_api_key must not be empty")
        self._rest_api_key = rest_api_key
        self._transport = transport

    def search_category_raw(
        self,
        *,
        longitude: float,
        latitude: float,
        radius_m: int,
        page: int = 1,
        size: int = KAKAO_PAGE_SIZE,
        category_group_code: str = KAKAO_CAFE_CATEGORY_CODE,
    ) -> dict[str, Any]:
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("longitude/latitude are outside valid ranges")
        if not 1 <= radius_m <= KAKAO_MAX_RADIUS_M:
            raise ValueError(f"radius_m must be between 1 and {KAKAO_MAX_RADIUS_M}")
        if not 1 <= page <= KAKAO_MAX_PAGES:
            raise ValueError(f"page must be between 1 and {KAKAO_MAX_PAGES}")
        if not 1 <= size <= KAKAO_PAGE_SIZE:
            raise ValueError(f"size must be between 1 and {KAKAO_PAGE_SIZE}")

        try:
            with httpx.Client(
                base_url=KAKAO_LOCAL_BASE_URL,
                timeout=httpx.Timeout(
                    HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT_SECONDS
                ),
                headers={
                    "Authorization": f"KakaoAK {self._rest_api_key}",
                    "User-Agent": HTTP_USER_AGENT,
                },
                transport=self._transport,
            ) as client:
                response = client.get(
                    KAKAO_CATEGORY_PATH,
                    params={
                        "category_group_code": category_group_code,
                        "x": longitude,
                        "y": latitude,
                        "radius": radius_m,
                        "page": page,
                        "size": size,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise KakaoAPIError(
                f"Kakao API returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise KakaoAPIError(
                f"Kakao request failed ({type(exc).__name__})"
            ) from None
        if not isinstance(payload, dict):
            raise KakaoAPIError("Kakao API response root must be a JSON object")
        return payload

    def search_category(self, **kwargs: Any) -> KakaoCategoryResponse:
        return KakaoCategoryResponse.model_validate(self.search_category_raw(**kwargs))


def parse_category(payload: dict[str, Any]) -> KakaoCategoryResponse:
    """Parse a stored fixture without making a network call."""

    return KakaoCategoryResponse.model_validate(payload)
