#!/usr/bin/env python3
"""Read-only dry-run report for Kakao-owned canonical cafe expansion."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import create_db_engine
from app.ingest.kakao_catalog_expansion import (
    CanonicalCafeIdentity,
    build_kakao_expansion,
)
from app.models import Cafe, CafeProviderPlace
from scripts.build_provider_cafe_catalog import read_kakao_cache
from scripts.cache_kakao_cafes import (
    DEFAULT_CACHE,
    manifest_path_for,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kakao-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--kakao-manifest", type=Path)
    parser.add_argument("--database-url")
    return parser


def build_database_report(
    session: Session,
    *,
    kakao_cache: Path,
    kakao_manifest: Path,
) -> dict[str, object]:
    """Audit against one DB snapshot without mutating it."""

    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))
    cafes = tuple(
        CanonicalCafeIdentity(
            canonical_id=row.id,
            name=row.name,
            latitude=row.lat,
            longitude=row.lng,
            road_address=row.road_address,
            phone=row.phone,
        )
        for row in session.execute(
            select(
                Cafe.id,
                Cafe.name,
                Cafe.lat,
                Cafe.lng,
                Cafe.road_address,
                Cafe.phone,
            )
            .where(Cafe.active.is_(True))
            .order_by(Cafe.id)
        )
    )
    provider_ids = tuple(
        session.scalars(
            select(CafeProviderPlace.provider_place_id)
            .where(CafeProviderPlace.provider == "kakao")
            .order_by(CafeProviderPlace.provider_place_id)
        )
    )
    build = build_kakao_expansion(
        read_kakao_cache(kakao_cache, kakao_manifest),
        cafes,
        provider_ids,
    )
    candidate_ids = {
        candidate.canonical_source_id for candidate in build.candidates
    }
    return {
        "mode": "read-only-dry-run",
        "canonical_source_for_candidates": "kakao",
        "report": asdict(build.report),
        "candidate_sample": [
            {
                "canonical_source": candidate.canonical_source,
                "canonical_source_id": candidate.canonical_source_id,
                "name": candidate.name,
                "latitude": candidate.latitude,
                "longitude": candidate.longitude,
                "direct_url": candidate.direct_url,
            }
            for candidate in build.candidates[:20]
        ],
        "conflict_sample": [
            {
                **asdict(conflict),
                "disposition": (
                    "advisory_included"
                    if conflict.kakao_place_id in candidate_ids
                    else "blocking_excluded"
                ),
            }
            for conflict in build.conflicts[:20]
        ],
        "apply_gate": "HUMAN approval required; this command has no apply mode",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    kakao_manifest = args.kakao_manifest or manifest_path_for(args.kakao_cache)
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            payload = build_database_report(
                session,
                kakao_cache=args.kakao_cache,
                kakao_manifest=kakao_manifest,
            )
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except Exception as exc:
        print(
            f"Kakao expansion report failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
