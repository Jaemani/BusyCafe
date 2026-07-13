#!/usr/bin/env python3
"""Dry-run a curated cafe catalog seed; writes require explicit --apply."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import OVERTURE_RELEASE, SEOUL_BBOX
from app.database import create_db_engine
from app.ingest.curated_cafe_catalog import iter_curated_records
from app.ingest.overture_places import OvertureCafeRecord, OvertureSeedReport, seed_overture_cafes
from scripts.build_curated_cafe_catalog import DEFAULT_OUTPUT


class CuratedSeedError(RuntimeError):
    """Raised before apply when a curated stage would remove active cafes."""


@dataclass(frozen=True, slots=True)
class CuratedSeedStage:
    preflight: OvertureSeedReport
    applied: OvertureSeedReport | None


def stage_curated_seed(
    session: Session,
    records: Sequence[OvertureCafeRecord],
    *,
    release: str,
    apply: bool,
) -> CuratedSeedStage:
    preflight = seed_overture_cafes(
        session,
        records,
        release=release,
        scope_bbox=SEOUL_BBOX,
        dry_run=True,
    )
    if not apply:
        return CuratedSeedStage(preflight=preflight, applied=None)
    if preflight.deactivated_count > 0:
        raise CuratedSeedError(
            "refusing apply: curated catalog would deactivate "
            f"{preflight.deactivated_count} active cafes"
        )
    applied = seed_overture_cafes(
        session,
        records,
        release=release,
        scope_bbox=SEOUL_BBOX,
        dry_run=False,
    )
    return CuratedSeedStage(preflight=preflight, applied=applied)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--database-url")
    parser.add_argument("--release", default=OVERTURE_RELEASE)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        records = tuple(iter_curated_records(args.cache))
        engine = create_db_engine(args.database_url)
        try:
            with Session(engine) as session:
                stage = stage_curated_seed(
                    session,
                    records,
                    release=args.release,
                    apply=args.apply,
                )
        finally:
            engine.dispose()
    except Exception as exc:
        print(f"seed failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    report = stage.applied or stage.preflight
    print(f"mode: {'write' if stage.applied is not None else 'dry-run'}")
    print(
        "inserted/updated/unchanged/deactivated: "
        f"{report.inserted_count}/{report.updated_count}/"
        f"{report.unchanged_count}/{report.deactivated_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
