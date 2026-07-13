from __future__ import annotations

from app.config import NAVER_VERIFY_QUERY, Settings
from app.schemas import NaverLocalResponse
from pydantic import SecretStr
import pytest

from scripts.probe_naver_local import main, probe


class FakeClient:
    def __init__(self, response: NaverLocalResponse) -> None:
        self.response = response
        self.queries: list[str] = []
        self.closed = False

    def search_local(self, query: str) -> NaverLocalResponse:
        self.queries.append(query)
        return self.response

    def close(self) -> None:
        self.closed = True


def _response(*, display: int = 1, link: str = "") -> NaverLocalResponse:
    return NaverLocalResponse.model_validate(
        {
            "total": 1,
            "start": 1,
            "display": display,
            "items": [
                {
                    "title": "스타벅스 더종로R점",
                    "link": link,
                    "roadAddress": "서울 종로구 종로 51",
                }
            ],
        }
    )


def test_probe_makes_one_fixed_query_and_reports_direct_place_links() -> None:
    client = FakeClient(
        _response(link="https://map.naver.com/p/entry/place/123?entry=pll")
    )

    assert probe(client) == (1, 1)
    assert client.queries == [NAVER_VERIFY_QUERY]


def test_probe_does_not_count_external_homepage_as_place_link() -> None:
    assert probe(FakeClient(_response(link="https://example.com"))) == (1, 0)


def test_probe_rejects_display_item_count_mismatch() -> None:
    with pytest.raises(ValueError, match="display count"):
        probe(FakeClient(_response(display=2)))


def test_main_requires_both_credentials_without_constructing_client() -> None:
    with pytest.raises(SystemExit, match="NAVER_CLIENT_ID.*NAVER_CLIENT_SECRET"):
        main(
            settings_loader=lambda: Settings(
                naver_client_id=SecretStr("id"),
                naver_client_secret=None,
                database_url="unused",
            ),
            client_factory=lambda _id, _secret: pytest.fail("must not construct"),
        )


def test_main_closes_client_and_never_prints_secrets(capsys) -> None:
    client = FakeClient(_response())
    received: list[tuple[str, str]] = []

    def factory(client_id: str, secret: str) -> FakeClient:
        received.append((client_id, secret))
        return client

    assert main(
        settings_loader=lambda: Settings(
            naver_client_id=SecretStr("private-id"),
            naver_client_secret=SecretStr("private-secret"),
            database_url="unused",
        ),
        client_factory=factory,
    ) == 0

    output = capsys.readouterr().out
    assert received == [("private-id", "private-secret")]
    assert client.closed
    assert "private-id" not in output
    assert "private-secret" not in output
