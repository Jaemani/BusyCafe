#!/usr/bin/env python3
"""Build immutable curated cafe JSONL from local permit and Overture caches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import OVERTURE_RELEASE  # noqa: E402
from app.ingest.curated_cafe_catalog import (  # noqa: E402
    CuratedCatalogBuild,
    build_curated_catalog,
    serialize_curated_records,
)
from app.ingest.overture_places import iter_cached_records  # noqa: E402
from scripts.cache_refreshment_candidates import (  # noqa: E402
    DEFAULT_CACHE as DEFAULT_PERMIT_CACHE,
    read_candidate_cache,
)


DEFAULT_OVERTURE_CACHE = (
    BACKEND_DIR / "data" / f"overture-seoul-cafes-{OVERTURE_RELEASE}-min050.parquet"
)
DEFAULT_OUTPUT = BACKEND_DIR / "data" / f"curated-seoul-cafes-{OVERTURE_RELEASE}.jsonl"


def build_manifest(build: CuratedCatalogBuild, cache_bytes: bytes) -> dict[str, object]:
    return {
        **asdict(build.report),
        "cache_sha256": hashlib.sha256(cache_bytes).hexdigest(),
        "overture_release": OVERTURE_RELEASE,
    }


def _write_temp(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def publish_build(output: Path, manifest: Path, build: CuratedCatalogBuild) -> None:
    if output == manifest:
        raise ValueError("output and manifest paths must differ")
    for path in (output, manifest):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {path}")
    cache_bytes = serialize_curated_records(build.records)
    manifest_bytes = (
        json.dumps(
            build_manifest(build, cache_bytes),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    output_temp = _write_temp(output, cache_bytes)
    manifest_temp = _write_temp(manifest, manifest_bytes)
    output_published = False
    try:
        os.link(output_temp, output)
        output_published = True
        os.link(manifest_temp, manifest)
    except BaseException:
        if output_published:
            output.unlink(missing_ok=True)
        raise
    finally:
        output_temp.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overture-cache", type=Path, default=DEFAULT_OVERTURE_CACHE)
    parser.add_argument("--permit-cache", type=Path, default=DEFAULT_PERMIT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = args.manifest or args.output.with_suffix(".manifest.json")
    for path in (args.output, manifest):
        if path.exists():
            print(f"build failed: refusing to overwrite existing output: {path}", file=sys.stderr)
            return 1
    try:
        build = build_curated_catalog(
            tuple(iter_cached_records(args.overture_cache)),
            read_candidate_cache(args.permit_cache),
        )
        publish_build(args.output, manifest, build)
    except Exception as exc:
        print(f"build failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(f"curated catalog created: {args.output}")
    print(
        f"curated={build.report.curated_count} "
        f"incremental={build.report.incremental_low_confidence_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
