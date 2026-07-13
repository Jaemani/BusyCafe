#!/usr/bin/env python3
"""Sweep Kakao CE7 over Seoul; dry-run unless ``--apply`` publishes cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.kakao_local import KakaoLocalClient  # noqa: E402
from app.config import (  # noqa: E402
    KAKAO_CACHE_DIR,
    KAKAO_CACHE_FILENAME,
    KAKAO_CAFE_CATEGORY_CODE,
    KAKAO_SWEEP_MAX_CALLS,
    KAKAO_SWEEP_MAX_DEPTH,
    KAKAO_SWEEP_MIN_CELL_SPAN_DEG,
    SEOUL_BBOX,
    get_settings,
)
from app.ingest.kakao_places import KakaoSweepReport, sweep_kakao_cafes  # noqa: E402
from app.schemas import KakaoPlace  # noqa: E402


DEFAULT_CACHE = KAKAO_CACHE_DIR / KAKAO_CACHE_FILENAME
MANIFEST_SCHEMA_VERSION = 1


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        parts = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox must contain four numbers") from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must contain four numbers")
    min_lng, min_lat, max_lng, max_lat = parts
    if not (
        -180 <= min_lng < max_lng <= 180
        and -90 <= min_lat < max_lat <= 90
    ):
        raise argparse.ArgumentTypeError("bbox coordinates are invalid")
    return min_lng, min_lat, max_lng, max_lat


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        type=_parse_bbox,
        default=SEOUL_BBOX,
        help="minLng,minLat,maxLng,maxLat (default: configured Seoul bbox)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--max-depth", type=int, default=KAKAO_SWEEP_MAX_DEPTH)
    parser.add_argument(
        "--min-cell-span-deg",
        type=float,
        default=KAKAO_SWEEP_MIN_CELL_SPAN_DEG,
    )
    parser.add_argument("--max-calls", type=int, default=KAKAO_SWEEP_MAX_CALLS)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="atomically publish cache and aggregate manifest (default dry-run)",
    )
    return parser


def _record_line(record: KakaoPlace) -> bytes:
    payload = record.model_dump(mode="json", by_alias=True)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def cache_sha256(records: Sequence[KakaoPlace]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(_record_line(record))
    return digest.hexdigest()


def build_manifest(
    report: KakaoSweepReport,
    *,
    bbox: tuple[float, float, float, float],
    generated_at: datetime,
) -> dict[str, Any]:
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    unresolved_by_reason = Counter(item.reason for item in report.unresolved)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "source": "Kakao Local category search",
        "category_group_code": KAKAO_CAFE_CATEGORY_CODE,
        "bbox": list(bbox),
        "complete": report.complete,
        "record_count": len(report.records),
        "cache_sha256": cache_sha256(report.records),
        "api_calls": report.api_calls,
        "http_attempts": report.http_attempts,
        "source_documents": report.source_documents,
        "duplicate_documents": report.duplicate_documents,
        "completed_leaf_cells": report.completed_leaf_cells,
        "split_cells": report.split_cells,
        "max_depth_visited": report.max_depth_visited,
        "unresolved_count": len(report.unresolved),
        "unresolved_by_reason": dict(sorted(unresolved_by_reason.items())),
    }


def manifest_path_for(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".manifest.json")


def _temporary_path(destination: Path, *, mode: str) -> tuple[Any, Path]:
    temporary = NamedTemporaryFile(
        mode=mode,
        encoding="utf-8" if "b" not in mode else None,
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".part",
        delete=False,
    )
    return temporary, Path(temporary.name)


def publish_cache(
    records: Sequence[KakaoPlace],
    manifest: dict[str, Any],
    output: Path,
) -> tuple[Path, Path]:
    """Publish cache first and hash-bearing manifest last using atomic replaces."""

    if not manifest.get("complete"):
        raise ValueError("refusing to publish an incomplete Kakao sweep")
    expected_digest = cache_sha256(records)
    if manifest.get("cache_sha256") != expected_digest:
        raise ValueError("manifest cache hash does not match records")

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_path_for(output)
    cache_temp_file, cache_temp_path = _temporary_path(output, mode="wb")
    manifest_temp_file, manifest_temp_path = _temporary_path(
        manifest_path, mode="w"
    )
    try:
        with cache_temp_file as destination:
            for record in records:
                destination.write(_record_line(record))
            destination.flush()
            os.fsync(destination.fileno())
        with manifest_temp_file as destination:
            json.dump(manifest, destination, ensure_ascii=False, sort_keys=True, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(cache_temp_path, output)
        os.replace(manifest_temp_path, manifest_path)
    finally:
        cache_temp_path.unlink(missing_ok=True)
        manifest_temp_path.unlink(missing_ok=True)
    return output, manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    if settings.kakao_rest_key is None:
        raise SystemExit("KAKAO_REST_KEY is required")
    key = settings.kakao_rest_key.get_secret_value()
    with KakaoLocalClient(key) as client:
        report = sweep_kakao_cafes(
            client,
            args.bbox,
            max_depth=args.max_depth,
            min_cell_span_deg=args.min_cell_span_deg,
            max_calls=args.max_calls,
        )
    manifest = build_manifest(
        report,
        bbox=args.bbox,
        generated_at=datetime.now(UTC),
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    if not report.complete:
        for cell in report.unresolved:
            print(
                f"unresolved: {cell.path} depth={cell.depth} "
                f"reason={cell.reason} total_count={cell.total_count} "
                f"rect={cell.rect}",
                file=sys.stderr,
            )
        return 2
    if args.apply:
        output, manifest_path = publish_cache(report.records, manifest, args.output)
        print(f"published cache: {output}")
        print(f"published manifest: {manifest_path}")
    else:
        print("dry-run: cache not written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
