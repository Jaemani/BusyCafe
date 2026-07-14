#!/usr/bin/env python3
"""Strict Naver Place-link enrichment; DB writes require ``--apply``."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.naver_local import NaverLocalClient
from app.config import NAVER_LOCAL_DAILY_CALL_LIMIT, Settings, get_settings
from app.database import create_db_engine
from app.ingest.naver_place_links import build_naver_query, match_naver_place
from app.models import Cafe, CafeProviderPlace
from app.schemas import NaverLocalResponse


DEFAULT_MAX_CAFES = 100
MATCH_METHOD = "naver_local_exact_name_road_address"


class SearchClient(Protocol):
    request_count: int

    def search_local(self, query: str) -> NaverLocalResponse: ...


@dataclass(frozen=True, slots=True)
class NaverSeedReport:
    eligible_count: int
    searched_count: int
    matched_count: int
    inserted_count: int
    status_counts: tuple[tuple[str, int], ...]
    request_count: int
    last_searched_cafe_id: int | None
    dry_run: bool
    accepted_links: tuple[NaverAcceptedLink, ...]


@dataclass(frozen=True, slots=True)
class NaverAcceptedLink:
    cafe_id: int
    cafe_name: str
    provider_place_id: str
    detail_url: str


@dataclass(frozen=True, slots=True)
class _DiscoveredLink:
    cafe: Cafe
    provider_place_id: str
    detail_url: str


def seed_naver_place_links(
    session: Session,
    client: SearchClient,
    *,
    dry_run: bool,
    max_cafes: int = DEFAULT_MAX_CAFES,
    after_cafe_id: int = 0,
    now: datetime | None = None,
) -> NaverSeedReport:
    """Search one deterministic batch, then add only collision-free exact links."""

    if not 1 <= max_cafes <= NAVER_LOCAL_DAILY_CALL_LIMIT:
        raise ValueError(
            f"max_cafes must be between 1 and {NAVER_LOCAL_DAILY_CALL_LIMIT}"
        )
    if after_cafe_id < 0:
        raise ValueError("after_cafe_id must be >= 0")
    seen_at = now or datetime.now(UTC)
    if seen_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    seen_at = seen_at.astimezone(UTC)

    provider_places = tuple(session.scalars(select(CafeProviderPlace)))
    providers_by_key = {
        (place.provider, place.provider_place_id): place for place in provider_places
    }
    if len(providers_by_key) != len(provider_places):
        raise ValueError("database contains duplicate provider place IDs")
    cafes_with_naver = {
        place.cafe_id for place in provider_places if place.provider == "naver"
    }
    cafes = tuple(
        session.scalars(
            select(Cafe)
            .where(Cafe.active.is_(True), Cafe.id > after_cafe_id)
            .order_by(Cafe.id)
        )
    )
    eligible = tuple(
        cafe
        for cafe in cafes
        if cafe.id not in cafes_with_naver and bool(cafe.road_address)
    )
    batch = eligible[:max_cafes]

    statuses: Counter[str] = Counter()
    discovered: list[_DiscoveredLink] = []
    for cafe in batch:
        assert cafe.road_address is not None
        response = client.search_local(build_naver_query(cafe.name, cafe.road_address))
        result = match_naver_place(
            cafe_name=cafe.name,
            cafe_road_address=cafe.road_address,
            response=response,
        )
        statuses[result.status] += 1
        if result.match is not None:
            discovered.append(
                _DiscoveredLink(
                    cafe=cafe,
                    provider_place_id=result.match.provider_place_id,
                    detail_url=result.match.detail_url,
                )
            )

    discovered_by_provider: dict[str, list[_DiscoveredLink]] = defaultdict(list)
    for item in discovered:
        discovered_by_provider[item.provider_place_id].append(item)

    accepted: list[_DiscoveredLink] = []
    for provider_place_id in sorted(discovered_by_provider):
        candidates = discovered_by_provider[provider_place_id]
        if len(candidates) != 1:
            statuses["reverse_collision"] += len(candidates)
            continue
        candidate = candidates[0]
        owner = providers_by_key.get(("naver", provider_place_id))
        if owner is not None and owner.cafe_id != candidate.cafe.id:
            statuses["existing_owner_collision"] += 1
            continue
        accepted.append(candidate)

    accepted.sort(key=lambda item: (item.cafe.id, item.provider_place_id))

    if not dry_run:
        session.add_all(
            [
                CafeProviderPlace(
                    cafe_id=item.cafe.id,
                    provider="naver",
                    provider_place_id=item.provider_place_id,
                    detail_url=item.detail_url,
                    active=True,
                    match_method=MATCH_METHOD,
                    match_distance_m=None,
                    verified_at=seen_at,
                    last_seen_at=seen_at,
                )
                for item in accepted
            ]
        )
        session.commit()

    return NaverSeedReport(
        eligible_count=len(eligible),
        searched_count=len(batch),
        matched_count=len(discovered),
        inserted_count=len(accepted),
        status_counts=tuple(sorted(statuses.items())),
        request_count=client.request_count,
        last_searched_cafe_id=batch[-1].id if batch else None,
        dry_run=dry_run,
        accepted_links=tuple(
            NaverAcceptedLink(
                cafe_id=item.cafe.id,
                cafe_name=item.cafe.name,
                provider_place_id=item.provider_place_id,
                detail_url=item.detail_url,
            )
            for item in accepted
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--max-cafes", type=int, default=DEFAULT_MAX_CAFES)
    parser.add_argument("--after-cafe-id", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_loader: Callable[[], Settings] = get_settings,
    client_factory: Callable[[str, str], SearchClient] = NaverLocalClient,
) -> int:
    args = _parser().parse_args(argv)
    settings: Settings = settings_loader()
    if settings.naver_client_id is None or settings.naver_client_secret is None:
        print(
            "seed failed: NAVER_CLIENT_ID and NAVER_CLIENT_SECRET are required",
            file=sys.stderr,
        )
        return 1
    engine = create_db_engine(args.database_url)
    client = client_factory(
        settings.naver_client_id.get_secret_value(),
        settings.naver_client_secret.get_secret_value(),
    )
    try:
        with Session(engine) as session:
            report = seed_naver_place_links(
                session,
                client,
                dry_run=not args.apply,
                max_cafes=args.max_cafes,
                after_cafe_id=args.after_cafe_id,
            )
    except Exception as exc:
        print(f"seed failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
        engine.dispose()
    print(f"mode: {'write' if args.apply else 'dry-run'}")
    print(
        "eligible/searched/matched/inserted: "
        f"{report.eligible_count}/{report.searched_count}/"
        f"{report.matched_count}/{report.inserted_count}"
    )
    print(f"requests: {report.request_count}")
    print(f"last searched cafe id: {report.last_searched_cafe_id}")
    print("statuses: " + ", ".join(f"{k}={v}" for k, v in report.status_counts))
    for accepted in report.accepted_links:
        print(
            "accepted: "
            + json.dumps(
                {
                    "cafe_id": accepted.cafe_id,
                    "cafe_name": accepted.cafe_name,
                    "detail_url": accepted.detail_url,
                    "provider_place_id": accepted.provider_place_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
