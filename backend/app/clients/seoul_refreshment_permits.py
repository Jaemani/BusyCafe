"""Official Seoul refreshment-food permit client (OA-16095).

The source is a recall/completeness input, not a cafe ledger by itself:
``UPTAENM`` includes coffee shops alongside convenience stores, fast food,
general prepared food, and other non-cafe businesses. This module therefore
preserves the raw category and never decides which rows become cafes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import isfinite
from typing import Any
from urllib.parse import quote

import httpx
from pyproj import Transformer

from app.config import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_MAX_CONNECTIONS,
    HTTP_MAX_KEEPALIVE_CONNECTIONS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    SEOUL_API_BASE_URL,
    SEOUL_REFRESHMENT_PERMIT_CRS,
    SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE,
    SEOUL_REFRESHMENT_PERMIT_SERVICE,
    SEOUL_REFRESHMENT_PERMIT_WGS84_CRS,
)
from app.schemas import SeoulRefreshmentPermitPage


class SeoulRefreshmentPermitAPIError(RuntimeError):
    """Raised for HTTP, semantic, envelope, or coordinate errors."""


@dataclass(frozen=True, slots=True)
class WGS84Point:
    latitude: float
    longitude: float


_TO_WGS84 = Transformer.from_crs(
    SEOUL_REFRESHMENT_PERMIT_CRS,
    SEOUL_REFRESHMENT_PERMIT_WGS84_CRS,
    always_xy=True,
)


def epsg5174_to_wgs84(projected_x_m: float, projected_y_m: float) -> WGS84Point:
    """Convert official EPSG:5174 X/Y metres to WGS84 latitude/longitude."""

    if not isfinite(projected_x_m) or not isfinite(projected_y_m):
        raise SeoulRefreshmentPermitAPIError("projected coordinates must be finite")
    longitude, latitude = _TO_WGS84.transform(projected_x_m, projected_y_m)
    if not isfinite(latitude) or not isfinite(longitude):
        raise SeoulRefreshmentPermitAPIError("coordinate conversion was not finite")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise SeoulRefreshmentPermitAPIError(
            "coordinate conversion is outside WGS84 bounds"
        )
    return WGS84Point(latitude=latitude, longitude=longitude)


def _validate_page_bounds(start_index: int, end_index: int) -> None:
    if start_index < 1:
        raise ValueError("start_index must be >= 1")
    if end_index < start_index:
        raise ValueError("end_index must be >= start_index")
    count = end_index - start_index + 1
    if count > SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE:
        raise ValueError(
            "one permit request may contain at most "
            f"{SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE} rows"
        )


def _build_url(api_key: str, start_index: int, end_index: int) -> str:
    _validate_page_bounds(start_index, end_index)
    parts = (
        quote(api_key, safe=""),
        "json",
        SEOUL_REFRESHMENT_PERMIT_SERVICE,
        str(start_index),
        str(end_index),
    )
    return f"{SEOUL_API_BASE_URL.rstrip('/')}/{'/'.join(parts)}/"


def parse_permit_page(payload: dict[str, Any]) -> SeoulRefreshmentPermitPage:
    """Parse a stored measured response without network access."""

    envelope = payload.get(SEOUL_REFRESHMENT_PERMIT_SERVICE)
    if not isinstance(envelope, dict):
        result = payload.get("RESULT")
        if isinstance(result, dict):
            code = result.get("CODE", "unknown")
            message = result.get("MESSAGE", "unknown Seoul API error")
            raise SeoulRefreshmentPermitAPIError(
                f"Seoul permit API error {code}: {message}"
            )
        raise SeoulRefreshmentPermitAPIError(
            f"response has no {SEOUL_REFRESHMENT_PERMIT_SERVICE} envelope"
        )

    result = envelope.get("RESULT")
    if not isinstance(result, dict):
        raise SeoulRefreshmentPermitAPIError("response has no RESULT object")
    code = result.get("CODE")
    message = result.get("MESSAGE")
    if code != "INFO-000":
        raise SeoulRefreshmentPermitAPIError(
            f"Seoul permit API error {code or 'unknown'}: "
            f"{message or 'unknown Seoul API error'}"
        )

    rows = envelope.get("row")
    if not isinstance(rows, list):
        raise SeoulRefreshmentPermitAPIError("response row must be a list")
    try:
        return SeoulRefreshmentPermitPage.model_validate(
            {
                "total_count": envelope.get("list_total_count"),
                "result_code": code,
                "result_message": message or "",
                "rows": rows,
            }
        )
    except ValueError as exc:
        raise SeoulRefreshmentPermitAPIError(
            f"invalid Seoul permit response schema ({type(exc).__name__})"
        ) from exc


class SeoulRefreshmentPermitClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        # API keys are path segments. Never allow httpx/httpcore INFO logs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._api_key = api_key
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT_SECONDS
            ),
            limits=httpx.Limits(
                max_connections=HTTP_MAX_CONNECTIONS,
                max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
            ),
            headers={"User-Agent": HTTP_USER_AGENT},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SeoulRefreshmentPermitClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def fetch_page_raw(
        self, start_index: int, end_index: int
    ) -> dict[str, Any]:
        url = _build_url(self._api_key, start_index, end_index)
        try:
            response = self._client.get(url)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise SeoulRefreshmentPermitAPIError(
                f"Seoul permit API returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise SeoulRefreshmentPermitAPIError(
                f"Seoul permit request failed ({type(exc).__name__})"
            ) from None
        except ValueError:
            raise SeoulRefreshmentPermitAPIError(
                "Seoul permit API returned invalid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise SeoulRefreshmentPermitAPIError(
                "Seoul permit response root must be a JSON object"
            )
        return payload

    def fetch_page(
        self, start_index: int, end_index: int
    ) -> SeoulRefreshmentPermitPage:
        return parse_permit_page(self.fetch_page_raw(start_index, end_index))
