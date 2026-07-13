#!/usr/bin/env python3
"""Read-only one-request probe for the official Naver local-search API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.clients.naver_local import NaverLocalClient
from app.config import NAVER_VERIFY_QUERY, Settings, get_settings
from app.ingest.naver_place_links import canonical_naver_place_link
from app.schemas import NaverLocalResponse


class ProbeClient(Protocol):
    def search_local(self, query: str) -> NaverLocalResponse: ...


def probe(client: ProbeClient) -> tuple[int, int]:
    response = client.search_local(NAVER_VERIFY_QUERY)
    if response.display != len(response.items):
        raise ValueError("Naver local-search display count does not match items")
    direct_links = sum(
        canonical_naver_place_link(item.link) is not None
        for item in response.items
    )
    return len(response.items), direct_links


def main(
    *,
    settings_loader: Callable[[], Settings] = get_settings,
    client_factory: Callable[[str, str], ProbeClient] = NaverLocalClient,
) -> int:
    settings = settings_loader()
    if settings.naver_client_id is None or settings.naver_client_secret is None:
        raise SystemExit(
            "NAVER_CLIENT_ID and NAVER_CLIENT_SECRET are required for the probe"
        )
    client = client_factory(
        settings.naver_client_id.get_secret_value(),
        settings.naver_client_secret.get_secret_value(),
    )
    try:
        result_count, direct_place_link_count = probe(client)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    print(
        "Naver local-search probe succeeded "
        f"(results={result_count}, direct_place_links={direct_place_link_count})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
