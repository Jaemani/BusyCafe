from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.clients.seoul_citydata import SeoulAPIError
from app.config import SEOUL_VERIFY_AREA_NAME, Settings
from scripts.probe_seoul_citydata import main, probe


class FakeProbeClient:
    def __init__(self, population: Any) -> None:
        self.population = population
        self.calls: list[str] = []

    def fetch_population(self, area_name: str) -> Any:
        self.calls.append(area_name)
        return self.population


def test_probe_fetches_exactly_one_fixed_hotspot() -> None:
    client = FakeProbeClient(SimpleNamespace(area_name=SEOUL_VERIFY_AREA_NAME))

    probe(client)

    assert client.calls == [SEOUL_VERIFY_AREA_NAME]


def test_probe_rejects_mismatched_response_target() -> None:
    with pytest.raises(SeoulAPIError, match="target mismatch"):
        probe(FakeProbeClient(SimpleNamespace(area_name="다른 장소")))


def test_main_requires_only_seoul_key_and_prints_no_secret_or_url(capsys) -> None:
    secret = "probe-secret-must-not-appear"
    client = FakeProbeClient(SimpleNamespace(area_name=SEOUL_VERIFY_AREA_NAME))
    received_keys: list[str] = []

    def client_factory(key: str) -> FakeProbeClient:
        received_keys.append(key)
        return client

    result = main(
        settings_loader=lambda: Settings(
            seoul_api_key=SecretStr(secret),
            database_url="not-a-database-url",
        ),
        client_factory=client_factory,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert received_keys == [secret]
    assert client.calls == [SEOUL_VERIFY_AREA_NAME]
    assert secret not in output
    assert "http" not in output.lower()


def test_main_rejects_missing_seoul_key_before_constructing_client() -> None:
    with pytest.raises(SystemExit, match="SEOUL_API_KEY is required"):
        main(
            settings_loader=lambda: Settings(
                seoul_api_key=None,
                database_url="not-a-database-url",
            ),
            client_factory=lambda _key: pytest.fail(
                "client must not be constructed"
            ),
        )
