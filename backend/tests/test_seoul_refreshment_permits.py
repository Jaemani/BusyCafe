from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.geo import haversine_m
from app.clients.seoul_refreshment_permits import (
    SeoulRefreshmentPermitAPIError,
    SeoulRefreshmentPermitClient,
    epsg5174_to_wgs84,
    parse_permit_page,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "seoul_refreshment_permits_sample.json"
)


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_parse_measured_permit_fixture_preserves_status_and_category() -> None:
    page = parse_permit_page(load_fixture())

    assert page.total_count == 146_243
    assert page.result_code == "INFO-000"
    assert len(page.rows) == 3

    active_cafe, closed_cafe, active_non_cafe = page.rows
    assert active_cafe.business_name == "스타벅스 서초우성사거리점"
    assert active_cafe.business_type == "커피숍"
    assert active_cafe.is_reported_open is True
    assert active_cafe.closure_date is None
    assert active_cafe.phone is None
    assert active_cafe.projected_x_m == pytest.approx(202_513.119414067)
    assert active_cafe.projected_y_m == pytest.approx(443_478.806945055)
    assert active_cafe.facility_total_scope_raw == "125.50000"
    assert active_cafe.facility_total_scope_decimal == Decimal("125.50000")
    assert active_cafe.site_area_raw == "125.50"
    assert active_cafe.site_area_decimal == Decimal("125.50")

    assert closed_cafe.business_type == "커피숍"
    assert closed_cafe.trade_status_name == "폐업"
    assert closed_cafe.detail_status_name == "폐업"
    assert closed_cafe.closure_date == "2023-12-29"
    assert closed_cafe.is_reported_open is False
    assert closed_cafe.facility_total_scope_decimal == Decimal("0")
    assert closed_cafe.site_area_raw == "not-numeric"
    assert closed_cafe.site_area_decimal is None

    # OA-16095 is a broad permit source, not a cafe-only catalog.
    assert active_non_cafe.business_type == "편의점"
    assert active_non_cafe.is_reported_open is True


def test_epsg5174_coordinate_matches_direct_place_crosscheck() -> None:
    point = epsg5174_to_wgs84(202_513.119414067, 443_478.806945055)

    assert point.latitude == pytest.approx(37.4935091, abs=1e-6)
    assert point.longitude == pytest.approx(127.0292067, abs=1e-6)
    # Kakao direct-place result measured for the same named store/address on
    # 2026-07-13. The nine-metre gap is consistent with entrance/parcel points.
    distance_m = haversine_m(
        point.latitude,
        point.longitude,
        37.4935368360249,
        127.029310303418,
    )
    assert distance_m == pytest.approx(9.7, abs=1.0)


def test_parse_rejects_semantic_error() -> None:
    with pytest.raises(SeoulRefreshmentPermitAPIError, match="INFO-100"):
        parse_permit_page(
            {"RESULT": {"CODE": "INFO-100", "MESSAGE": "invalid key"}}
        )


def test_client_builds_verified_service_and_page_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "secret-key" in url
        assert "/json/LOCALDATA_072405/1/1000/" in url
        return httpx.Response(200, json=load_fixture())

    with SeoulRefreshmentPermitClient(
        "secret-key", transport=httpx.MockTransport(handler)
    ) as client:
        page = client.fetch_page(1, 1000)
    assert page.total_count == 146_243


@pytest.mark.parametrize(
    ("start_index", "end_index"),
    [(0, 1), (2, 1), (1, 1001)],
)
def test_client_rejects_invalid_page_bounds(
    start_index: int, end_index: int
) -> None:
    client = SeoulRefreshmentPermitClient(
        "secret-key", transport=httpx.MockTransport(lambda _: None)
    )
    with pytest.raises(ValueError):
        client.fetch_page_raw(start_index, end_index)
    client.close()


def test_client_does_not_leak_path_api_key_on_http_error() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    client = SeoulRefreshmentPermitClient("do-not-leak", transport=transport)
    with pytest.raises(SeoulRefreshmentPermitAPIError) as caught:
        client.fetch_page_raw(1, 1)
    client.close()
    assert "do-not-leak" not in str(caught.value)
