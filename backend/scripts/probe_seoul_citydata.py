"""Read-only one-request connectivity probe for the Seoul city-data API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.clients.seoul_citydata import SeoulAPIError, SeoulCityDataClient
from app.config import SEOUL_VERIFY_AREA_NAME, Settings, get_settings
from app.schemas import SeoulAreaPopulation


class ProbeClient(Protocol):
    def fetch_population(self, area_name: str) -> SeoulAreaPopulation: ...


def probe(client: ProbeClient) -> None:
    """Fetch and validate exactly one fixed hotspot without persistence."""

    population = client.fetch_population(SEOUL_VERIFY_AREA_NAME)
    if population.area_name != SEOUL_VERIFY_AREA_NAME:
        raise SeoulAPIError("Seoul probe response target mismatch")


def main(
    *,
    settings_loader: Callable[[], Settings] = get_settings,
    client_factory: Callable[[str], ProbeClient] = SeoulCityDataClient,
) -> int:
    settings = settings_loader()
    if settings.seoul_api_key is None:
        raise SystemExit("SEOUL_API_KEY is required for the Seoul API probe")

    client = client_factory(settings.seoul_api_key.get_secret_value())
    probe(client)
    print("Seoul city-data probe succeeded (one fixed hotspot).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
