from __future__ import annotations

import httpx
import pytest

from app.clients.kakao_local import KakaoLocalClient
from app.clients.seoul_citydata import SeoulAPIError, SeoulCityDataClient, parse_population


RAW_PAYLOAD = {"unverified": "upstream response"}


def test_parse_seoul_rejects_semantic_api_error() -> None:
    with pytest.raises(SeoulAPIError, match="ERROR-301"):
        parse_population({"RESULT": {"CODE": "ERROR-301", "MESSAGE": "키 오류"}})


def test_parse_seoul_finds_nested_semantic_api_error() -> None:
    with pytest.raises(SeoulAPIError, match="ERROR-301"):
        parse_population(
            {"SeoulRtd.citydata_ppltn": [{"RESULT": {"CODE": "ERROR-301"}}]}
        )


def test_seoul_client_uses_mock_transport_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "secret-key" in str(request.url)
        assert "%EA%B4%91%ED%99%94%EB%AC%B8" in str(request.url)
        return httpx.Response(200, json=RAW_PAYLOAD)

    client = SeoulCityDataClient("secret-key", transport=httpx.MockTransport(handler))
    assert client.fetch_population_raw("광화문광장") == RAW_PAYLOAD


def test_seoul_client_does_not_leak_path_api_key_on_http_error() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    client = SeoulCityDataClient("do-not-leak", transport=transport)
    with pytest.raises(SeoulAPIError) as caught:
        client.fetch_population_raw("광화문광장")
    assert "do-not-leak" not in str(caught.value)


def test_kakao_client_sends_auth_header_and_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "KakaoAK rest-key"
        assert request.url.params["category_group_code"] == "CE7"
        assert request.url.params["radius"] == "1000"
        return httpx.Response(200, json=RAW_PAYLOAD)

    client = KakaoLocalClient("rest-key", transport=httpx.MockTransport(handler))
    response = client.search_category_raw(
        longitude=126.9769, latitude=37.5759, radius_m=1000
    )
    assert response == RAW_PAYLOAD


def test_kakao_client_validates_api_limits_before_request() -> None:
    client = KakaoLocalClient("rest-key", transport=httpx.MockTransport(lambda _: None))
    with pytest.raises(ValueError, match="radius_m"):
        client.search_category_raw(
            longitude=126.9769, latitude=37.5759, radius_m=20_001
        )
