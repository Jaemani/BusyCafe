from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.clients.naver_local import (
    NaverLocalAPIError,
    NaverLocalClient,
    parse_local_search,
)
from app.ingest.naver_place_links import (
    build_naver_query,
    canonical_naver_place_link,
    match_naver_place,
    normalize_exact,
)
from app.schemas import NaverLocalResponse


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _response(*items: dict[str, object]) -> NaverLocalResponse:
    return NaverLocalResponse.model_validate(
        {
            "lastBuildDate": "Mon, 13 Jul 2026 10:00:00 +0900",
            "total": len(items),
            "start": 1,
            "display": len(items),
            "items": list(items),
        }
    )


def _item(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "title": "<b>카페 봄</b>",
        "link": "https://map.naver.com/p/entry/place/123?entry=pll",
        "category": "카페,디저트>카페",
        "description": "",
        "telephone": "",
        "address": "서울 종로구 청진동 1",
        "roadAddress": "서울 종로구 종로 1",
        "mapx": "1269780000",
        "mapy": "375700000",
    }
    values.update(overrides)
    return values


def test_official_docs_fixture_parses_without_inventing_place_id() -> None:
    payload = json.loads(
        (FIXTURES_DIR / "naver_local_official_docs_sample.json").read_text(
            encoding="utf-8"
        )
    )

    response = parse_local_search(payload)

    assert response.total == 407
    assert response.items[0].road_address == "서울특별시 중구 을지로15길 6-5"
    assert canonical_naver_place_link(response.items[0].link) is None


def test_client_sends_official_headers_and_bounded_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Naver-Client-Id"] == "client-id"
        assert request.headers["X-Naver-Client-Secret"] == "client-secret"
        assert request.url.path == "/v1/search/local.json"
        assert request.url.params["query"] == "카페 봄 서울 종로구 종로 1"
        assert request.url.params["display"] == "5"
        assert request.url.params["start"] == "1"
        assert request.url.params["sort"] == "random"
        return httpx.Response(200, json=_response(_item()).model_dump(by_alias=True))

    with NaverLocalClient(
        "client-id",
        "client-secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.search_local("카페 봄 서울 종로구 종로 1")

    assert response.total == 1
    assert client.request_count == 1


def test_client_retries_transient_errors_without_leaking_secrets() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "99"})
        return httpx.Response(403, request=request)

    with NaverLocalClient(
        "do-not-leak-id",
        "do-not-leak-secret",
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
    ) as client:
        with pytest.raises(NaverLocalAPIError, match="HTTP 403") as caught:
            client.search_local_raw("카페 봄")
    assert attempts == 2
    assert delays == [30.0]
    assert "do-not-leak" not in str(caught.value)


def test_client_hard_stops_before_process_request_limit() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500)

    with NaverLocalClient(
        "id",
        "secret",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        max_retries=3,
        request_limit=2,
    ) as client:
        with pytest.raises(NaverLocalAPIError, match="request limit reached"):
            client.search_local_raw("카페 봄")

    assert attempts == 2
    assert client.request_count == 2


@pytest.mark.parametrize("display", [0, 6])
def test_client_rejects_invalid_display_before_request(display: int) -> None:
    with NaverLocalClient(
        "id", "secret", transport=httpx.MockTransport(lambda _: None)
    ) as client:
        with pytest.raises(ValueError, match="display"):
            client.search_local_raw("카페", display=display)
        assert client.request_count == 0


@pytest.mark.parametrize(
    ("value", "expected_id"),
    [
        ("https://map.naver.com/p/entry/place/123?entry=pll", "123"),
        ("https://m.map.naver.com/p/entry/place/456", "456"),
        ("https://m.place.naver.com/cafe/789/home?entry=pll", "789"),
        ("https://m.place.naver.com/restaurant/321", "321"),
    ],
)
def test_direct_urls_yield_canonical_entry_links(value: str, expected_id: str) -> None:
    match = canonical_naver_place_link(value)

    assert match is not None
    assert match.provider_place_id == expected_id
    assert match.detail_url == f"https://map.naver.com/p/entry/place/{expected_id}"


@pytest.mark.parametrize(
    "value",
    [
        "https://map.naver.com/p/search/카페/place/123",
        "https://search.naver.com/search.naver?query=카페",
        "https://naver.me/short",
        "https://example.com/123",
        "http://map.naver.com/p/entry/place/123",
        "https://map.naver.com/p/entry/place/not-numeric",
        "https://map.naver.com:443/p/entry/place/123",
        "https://map.naver.com:bad/p/entry/place/123",
    ],
)
def test_noncanonical_or_search_urls_are_rejected(value: str) -> None:
    assert canonical_naver_place_link(value) is None


def test_match_requires_normalized_exact_name_and_road_address_not_coordinates() -> None:
    result = match_naver_place(
        cafe_name="ＣＡＦＥ-봄",
        cafe_road_address="서울 종로구 종로-1",
        response=_response(
            _item(
                title="<b>cafe 봄</b>",
                roadAddress="서울 종로구 종로 1",
                mapx="0",
                mapy="0",
            )
        ),
    )

    assert result.status == "matched"
    assert result.match is not None
    assert result.match.provider_place_id == "123"
    assert normalize_exact(" ＣＡＦＥ-봄 ") == "cafe봄"


def test_match_rejects_fuzzy_name_address_mismatch_and_missing_direct_id() -> None:
    response = _response(
        _item(title="카페 보미"),
        _item(link="", title="카페 봄"),
        _item(roadAddress="서울 종로구 종로 2"),
    )

    result = match_naver_place(
        cafe_name="카페 봄",
        cafe_road_address="서울 종로구 종로 1",
        response=response,
    )

    assert result.status == "unmatched"
    assert result.match is None


def test_distinct_exact_place_ids_are_ambiguous() -> None:
    result = match_naver_place(
        cafe_name="카페 봄",
        cafe_road_address="서울 종로구 종로 1",
        response=_response(
            _item(link="https://map.naver.com/p/entry/place/123"),
            _item(link="https://map.naver.com/p/entry/place/456"),
        ),
    )

    assert result.status == "ambiguous"
    assert result.match is None
    assert result.exact_candidate_count == 2


def test_query_uses_only_name_and_road_address() -> None:
    assert build_naver_query(" 카페 봄 ", " 서울 종로구 종로 1 ") == (
        "카페 봄 서울 종로구 종로 1"
    )
