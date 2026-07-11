"""Client and response normalization for Seoul Real-time City Data."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.config import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    SEOUL_API_BASE_URL,
    SEOUL_CITYDATA_SERVICE,
    SEOUL_RESPONSE_END_INDEX,
    SEOUL_RESPONSE_FORMAT,
    SEOUL_RESPONSE_START_INDEX,
)
from app.schemas import SeoulAreaPopulation


class SeoulAPIError(RuntimeError):
    """Raised when the Seoul API returns an HTTP or semantic error."""


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT_SECONDS
    )


def _build_url(api_key: str, area_name: str) -> str:
    parts = (
        api_key,
        SEOUL_RESPONSE_FORMAT,
        SEOUL_CITYDATA_SERVICE,
        str(SEOUL_RESPONSE_START_INDEX),
        str(SEOUL_RESPONSE_END_INDEX),
        quote(area_name, safe=""),
    )
    return f"{SEOUL_API_BASE_URL.rstrip('/')}/{'/'.join(parts)}"


def _find_area_record(payload: Any) -> dict[str, Any]:
    """Find the measured flat population record in Seoul response envelopes."""

    if isinstance(payload, dict):
        if "AREA_NM" in payload and "AREA_CONGEST_LVL" in payload:
            return payload
        for value in payload.values():
            try:
                return _find_area_record(value)
            except SeoulAPIError:
                continue
    elif isinstance(payload, list):
        for value in payload:
            try:
                return _find_area_record(value)
            except SeoulAPIError:
                continue
    raise SeoulAPIError("Seoul response contains no population area record")


def _raise_for_api_error(payload: Any) -> None:
    if isinstance(payload, dict):
        result = payload.get("RESULT")
        if isinstance(result, dict):
            code = result.get("CODE") or result.get("RESULT.CODE")
            if code and code != "INFO-000":
                message = (
                    result.get("MESSAGE")
                    or result.get("RESULT.MESSAGE")
                    or "unknown Seoul API error"
                )
                raise SeoulAPIError(f"Seoul API error {code}: {message}")
        for value in payload.values():
            _raise_for_api_error(value)
    elif isinstance(payload, list):
        for value in payload:
            _raise_for_api_error(value)


class SeoulCityDataClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._transport = transport

    def fetch_population_raw(self, area_name: str) -> dict[str, Any]:
        if not area_name.strip():
            raise ValueError("area_name must not be empty")
        try:
            with httpx.Client(
                timeout=_timeout(),
                headers={"User-Agent": HTTP_USER_AGENT},
                transport=self._transport,
            ) as client:
                response = client.get(_build_url(self._api_key, area_name))
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            # The API key is embedded in the URL, so never propagate httpx's
            # exception string (which includes the request URL).
            raise SeoulAPIError(
                f"Seoul API returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise SeoulAPIError(
                f"Seoul request failed ({type(exc).__name__})"
            ) from None
        _raise_for_api_error(payload)
        if not isinstance(payload, dict):
            raise SeoulAPIError("Seoul API response root must be a JSON object")
        return payload

    def fetch_population(self, area_name: str) -> SeoulAreaPopulation:
        payload = self.fetch_population_raw(area_name)
        return SeoulAreaPopulation.model_validate(_find_area_record(payload))


def parse_population(payload: dict[str, Any]) -> SeoulAreaPopulation:
    """Parse a stored fixture without making a network call."""

    _raise_for_api_error(payload)
    return SeoulAreaPopulation.model_validate(_find_area_record(payload))
