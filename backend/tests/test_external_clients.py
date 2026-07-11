from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.clients.kakao_local import KakaoLocalClient, parse_category
from app.clients.seoul_citydata import SeoulAPIError, SeoulCityDataClient, parse_population


RAW_PAYLOAD = {"unverified": "upstream response"}
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_parse_seoul_measured_fixture() -> None:
    population = parse_population(load_fixture("citydata_sample.json"))

    assert population.area_name == "광화문광장"
    assert population.area_code == "POI088"
    assert population.congestion_level == "보통"
    assert population.numeric_level == 2
    assert population.population_min == 6500
    assert population.population_max == 7000
    assert population.observed_at == "2026-07-11 16:25"
    assert len(population.forecast) == 12
    assert {item.congestion_level for item in population.forecast} == {"보통", "여유"}


def test_parse_kakao_measured_fixture() -> None:
    response = parse_category(load_fixture("kakao_ce7_sample.json"))

    assert response.meta.total_count == 761
    assert response.meta.pageable_count == 45
    assert response.meta.is_end is False
    assert len(response.documents) == 15
    assert {place.category_group_code for place in response.documents} == {"CE7"}
    assert all(37 <= place.latitude <= 38 for place in response.documents)
    assert all(126 <= place.longitude <= 128 for place in response.documents)


def test_parse_seoul_rejects_semantic_api_error() -> None:
    with pytest.raises(SeoulAPIError, match="ERROR-301"):
        parse_population({"RESULT": {"CODE": "ERROR-301", "MESSAGE": "키 오류"}})


def test_parse_seoul_finds_nested_semantic_api_error() -> None:
    with pytest.raises(SeoulAPIError, match="ERROR-301"):
        parse_population(
            {"SeoulRtd.citydata_ppltn": [{"RESULT": {"CODE": "ERROR-301"}}]}
        )


def test_parse_seoul_rejects_measured_dotted_semantic_api_error_shape() -> None:
    with pytest.raises(SeoulAPIError, match="ERROR-301.*키 오류"):
        parse_population(
            {
                "RESULT": {
                    "RESULT.CODE": "ERROR-301",
                    "RESULT.MESSAGE": "키 오류",
                }
            }
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
