"""Cache and seed a verified Seoul Overture Places release.

The script never runs on map requests.  Downloading creates an immutable local
extract; seeding copies that extract to PostgreSQL, where the API serves it.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Sequence

from sqlalchemy.orm import Session

from app.config import OVERTURE_MIN_CONFIDENCE, OVERTURE_RELEASE
from app.database import create_db_engine
from app.ingest.overture_places import (
    cache_seoul_extract,
    iter_cached_records,
    seed_overture_cafes,
)


DEFAULT_CACHE = Path("data") / f"overture-seoul-cafes-{OVERTURE_RELEASE}.parquet"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument("--release", default=OVERTURE_RELEASE)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--download",
        action="store_true",
        help="download the bounded release into --cache; refuses overwrite",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="stop after creating the immutable cache extract",
    )
    parser.add_argument(
        "--min-confidence", type=float, default=OVERTURE_MIN_CONFIDENCE
    )
    parser.add_argument("--apply", action="store_true", help="write DB (default dry-run)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.download:
        count = cache_seoul_extract(
            args.cache,
            release=args.release,
            min_confidence=args.min_confidence,
        )
        print(f"cached Overture records: {count} -> {args.cache}")
        if args.download_only:
            return 0
    elif args.download_only:
        raise SystemExit("--download-only requires --download")
    records = tuple(iter_cached_records(args.cache))
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = seed_overture_cafes(
                session,
                records,
                release=args.release,
                dry_run=not args.apply,
            )
        print(
            "Overture cafe seed report\n"
            f"mode: {'write' if args.apply else 'dry-run'}\n"
            f"release: {args.release}\n"
            f"cache_sha256: {_sha256(args.cache)}\n"
            f"source/active: {report.source_count}/{report.active_count}\n"
            f"inserted/updated/unchanged/deactivated: "
            f"{report.inserted_count}/{report.updated_count}/"
            f"{report.unchanged_count}/{report.deactivated_count}"
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
