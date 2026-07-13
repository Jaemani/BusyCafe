#!/usr/bin/env python3
"""Read-only report comparing permit candidates with the local cafe catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ingest.permit_reconciliation import (  # noqa: E402
    CatalogPlace,
    ReconciliationResult,
    reconcile_candidates,
    select_unmatched_review_sample,
)
from app.ingest.overture_places import iter_cached_records  # noqa: E402
from scripts.cache_refreshment_candidates import (  # noqa: E402
    DEFAULT_CACHE,
    read_candidate_cache,
)


DEFAULT_DATABASE = BACKEND_DIR / "data" / "preview.db"
DEFAULT_REPORT = BACKEND_DIR / "data" / "permit_reconciliation_manifest.json"


def read_catalog_sqlite(path: Path) -> tuple[CatalogPlace, ...]:
    """Read active cafes through an SQLite read-only URI."""

    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            """
            SELECT id, name, lat, lng, primary_category, phone
            FROM cafes
            WHERE active = 1
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()
    return tuple(
        CatalogPlace(
            catalog_id=str(row[0]),
            name=str(row[1]),
            latitude=float(row[2]),
            longitude=float(row[3]),
            category=str(row[4]) if row[4] is not None else None,
            phone=str(row[5]) if row[5] is not None else None,
        )
        for row in rows
    )


def read_catalog_overture(path: Path) -> tuple[CatalogPlace, ...]:
    """Adapt one immutable Overture cache without network or DB access."""

    return tuple(
        CatalogPlace(
            catalog_id=record.overture_id,
            name=record.name,
            latitude=record.lat,
            longitude=record.lng,
            category=record.primary_category,
            phone=record.phone,
        )
        for record in iter_cached_records(path)
    )


def build_manifest(
    result: ReconciliationResult,
    *,
    candidate_cache_sha256: str,
    catalog_source: str = "sqlite",
    catalog_cache_sha256: str | None = None,
) -> dict[str, object]:
    """Build aggregate-only output without place IDs or business PII."""

    manifest: dict[str, object] = {
        "candidate_cache_sha256": candidate_cache_sha256,
        "catalog_source": catalog_source,
        "candidate_count": result.candidate_count,
        "catalog_count": result.catalog_count,
        "matched_count": len(result.matches),
        "ambiguous_count": len(result.ambiguous),
        "unmatched_count": len(result.unmatched),
        "candidate_category_counts": result.candidate_category_counts,
        "matched_category_counts": result.matched_category_counts,
        "ambiguous_category_counts": result.ambiguous_category_counts,
        "unmatched_category_counts": result.unmatched_category_counts,
        "match_rule_counts": result.match_rule_counts,
        "distance_check_count": result.distance_check_count,
    }
    if catalog_cache_sha256 is not None:
        manifest["catalog_cache_sha256"] = catalog_cache_sha256
    return manifest


def publish_manifest(path: Path, manifest: dict[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CACHE)
    catalog = parser.add_mutually_exclusive_group()
    catalog.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    catalog.add_argument(
        "--overture-cache",
        type=Path,
        help="read immutable local Parquet instead of SQLite; no network",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--review-unmatched",
        type=int,
        metavar="N",
        help="print deterministic unmatched JSONL sample; create no report",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.review_unmatched is None and args.output.exists():
        print(f"report failed: refusing to overwrite existing report: {args.output}", file=sys.stderr)
        return 1
    if args.review_unmatched is not None and args.review_unmatched < 0:
        raise SystemExit("--review-unmatched must be >= 0")
    try:
        candidates = read_candidate_cache(args.candidates)
        if args.overture_cache is not None:
            catalog = read_catalog_overture(args.overture_cache)
            catalog_source = "overture_cache"
            catalog_cache_sha256 = _sha256(args.overture_cache)
        else:
            catalog = read_catalog_sqlite(args.database)
            catalog_source = "sqlite"
            catalog_cache_sha256 = None
        result = reconcile_candidates(candidates, catalog)
        if args.review_unmatched is not None:
            sample = select_unmatched_review_sample(
                result.unmatched, args.review_unmatched
            )
            for candidate in sample:
                print(
                    json.dumps(
                        asdict(candidate),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            return 0
        publish_manifest(
            args.output,
            build_manifest(
                result,
                candidate_cache_sha256=_sha256(args.candidates),
                catalog_source=catalog_source,
                catalog_cache_sha256=catalog_cache_sha256,
            ),
        )
    except Exception as exc:
        print(f"report failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(f"reconciliation report created: {args.output}")
    print(
        f"matched={len(result.matches)} ambiguous={len(result.ambiguous)} "
        f"unmatched={len(result.unmatched)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
