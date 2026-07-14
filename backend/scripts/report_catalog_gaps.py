#!/usr/bin/env python3
"""Read-only aggregate audit of production cafe provider-link coverage."""

from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Protocol
from urllib.parse import urlparse

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import create_db_engine
from app.ingest.naver_place_links import canonical_naver_place_link
from app.models import Cafe, CafeProviderPlace
from scripts.seed_naver_place_links import DEFAULT_MAX_CAFES


REPORT_SCHEMA_VERSION = 1
_KAKAO_DETAIL_PATH = re.compile(r"/([0-9]+)/?")
_AUDITED_PROVIDERS = ("kakao", "naver")


class ReadOnlySession(Protocol):
    def get_bind(self): ...

    def execute(self, statement): ...


@dataclass(frozen=True, slots=True)
class ProviderCoverage:
    rows_for_active_cafes: int
    active_rows: int
    inactive_rows: int
    valid_direct_links: int
    invalid_direct_links: int
    active_cafes_with_identity: int
    active_cafes_with_valid_direct_link: int
    active_cafes_without_valid_direct_link: int


@dataclass(frozen=True, slots=True)
class OriginProviderCoverage:
    origin: str
    provider: str
    active_links: int
    valid_direct_links: int


@dataclass(frozen=True, slots=True)
class CatalogGapReport:
    schema_version: int
    active_cafes_total: int
    active_cafes_by_origin: dict[str, int]
    provider_coverage: dict[str, ProviderCoverage]
    origin_provider_coverage: tuple[OriginProviderCoverage, ...]
    naver_exact_match_eligible_total: int
    naver_exact_match_eligible_by_origin: dict[str, int]
    naver_exact_match_missing_road_address_total: int
    naver_exact_match_batch_size: int
    naver_exact_match_batch_count: int


def enforce_transaction_read_only(session: ReadOnlySession) -> None:
    """Make the production PostgreSQL transaction reject every write."""

    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))


def _direct_link_matches(
    provider: str,
    provider_place_id: str,
    detail_url: str | None,
) -> bool:
    if not detail_url:
        return False
    if provider == "naver":
        match = canonical_naver_place_link(detail_url)
        return match is not None and match.provider_place_id == provider_place_id
    if provider != "kakao":
        return False
    try:
        parsed = urlparse(detail_url)
        has_authority_override = bool(
            parsed.username or parsed.password or parsed.port
        )
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != "place.map.kakao.com"
        or has_authority_override
    ):
        return False
    matched = _KAKAO_DETAIL_PATH.fullmatch(parsed.path)
    return matched is not None and matched.group(1) == provider_place_id


def build_catalog_gap_report(
    session: Session,
    *,
    batch_size: int = DEFAULT_MAX_CAFES,
) -> CatalogGapReport:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    enforce_transaction_read_only(session)

    cafes = tuple(
        session.execute(
            select(
                Cafe.id,
                Cafe.origin_provider,
                Cafe.road_address,
            )
            .where(Cafe.active.is_(True))
            .order_by(Cafe.id)
        )
    )
    active_ids = {int(row.id) for row in cafes}
    origin_by_cafe = {int(row.id): str(row.origin_provider) for row in cafes}
    origins = Counter(str(row.origin_provider) for row in cafes)

    provider_rows = tuple(
        session.execute(
            select(
                CafeProviderPlace.cafe_id,
                CafeProviderPlace.provider,
                CafeProviderPlace.provider_place_id,
                CafeProviderPlace.detail_url,
                CafeProviderPlace.active,
            ).where(CafeProviderPlace.cafe_id.in_(active_ids))
        )
    ) if active_ids else ()

    rows_by_provider: Counter[str] = Counter()
    active_rows_by_provider: Counter[str] = Counter()
    valid_by_provider: Counter[str] = Counter()
    active_cafes_by_provider: dict[str, set[int]] = defaultdict(set)
    valid_cafes_by_provider: dict[str, set[int]] = defaultdict(set)
    all_cafes_by_provider: dict[str, set[int]] = defaultdict(set)
    matrix_active: Counter[tuple[str, str]] = Counter()
    matrix_valid: Counter[tuple[str, str]] = Counter()
    for row in provider_rows:
        cafe_id = int(row.cafe_id)
        provider = str(row.provider)
        rows_by_provider[provider] += 1
        all_cafes_by_provider[provider].add(cafe_id)
        if not bool(row.active):
            continue
        active_rows_by_provider[provider] += 1
        active_cafes_by_provider[provider].add(cafe_id)
        origin_key = (origin_by_cafe[cafe_id], provider)
        matrix_active[origin_key] += 1
        if _direct_link_matches(
            provider,
            str(row.provider_place_id),
            str(row.detail_url) if row.detail_url is not None else None,
        ):
            valid_by_provider[provider] += 1
            valid_cafes_by_provider[provider].add(cafe_id)
            matrix_valid[origin_key] += 1

    providers = tuple(sorted(set(_AUDITED_PROVIDERS) | set(rows_by_provider)))
    provider_coverage = {
        provider: ProviderCoverage(
            rows_for_active_cafes=rows_by_provider[provider],
            active_rows=active_rows_by_provider[provider],
            inactive_rows=(
                rows_by_provider[provider] - active_rows_by_provider[provider]
            ),
            valid_direct_links=valid_by_provider[provider],
            invalid_direct_links=(
                active_rows_by_provider[provider] - valid_by_provider[provider]
            ),
            active_cafes_with_identity=len(active_cafes_by_provider[provider]),
            active_cafes_with_valid_direct_link=len(
                valid_cafes_by_provider[provider]
            ),
            active_cafes_without_valid_direct_link=(
                len(cafes) - len(valid_cafes_by_provider[provider])
            ),
        )
        for provider in providers
    }
    matrix = tuple(
        OriginProviderCoverage(
            origin=origin,
            provider=provider,
            active_links=matrix_active[(origin, provider)],
            valid_direct_links=matrix_valid[(origin, provider)],
        )
        for origin in sorted(origins)
        for provider in providers
    )

    cafes_with_any_naver_row = all_cafes_by_provider["naver"]
    naver_candidates = [
        row for row in cafes if int(row.id) not in cafes_with_any_naver_row
    ]
    eligible = [row for row in naver_candidates if bool(row.road_address)]
    eligible_by_origin = Counter(str(row.origin_provider) for row in eligible)
    missing_address = len(naver_candidates) - len(eligible)

    return CatalogGapReport(
        schema_version=REPORT_SCHEMA_VERSION,
        active_cafes_total=len(cafes),
        active_cafes_by_origin=dict(sorted(origins.items())),
        provider_coverage=provider_coverage,
        origin_provider_coverage=matrix,
        naver_exact_match_eligible_total=len(eligible),
        naver_exact_match_eligible_by_origin=dict(sorted(eligible_by_origin.items())),
        naver_exact_match_missing_road_address_total=missing_address,
        naver_exact_match_batch_size=batch_size,
        naver_exact_match_batch_count=math.ceil(len(eligible) / batch_size),
    )


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1
    engine = create_db_engine(database_url)
    try:
        with Session(engine) as session:
            report = build_catalog_gap_report(session)
        print(
            json.dumps(
                asdict(report),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (SQLAlchemyError, ValueError) as exc:
        print(f"catalog gap report failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
