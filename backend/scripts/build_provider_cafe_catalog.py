#!/usr/bin/env python3
"""Build an immutable provider-link cafe catalog from local source caches."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (  # noqa: E402
    KAKAO_CAFE_CATEGORY_CODE,
    OVERTURE_RELEASE,
    SEOUL_BBOX,
)
from app.ingest.curated_cafe_catalog import iter_curated_records  # noqa: E402
from app.ingest.provider_cafe_catalog import (  # noqa: E402
    ProviderCatalogBuild,
    build_provider_cafe_catalog,
    serialize_provider_catalog,
    serialize_provider_catalog_manifest,
)
from app.schemas import KakaoPlace  # noqa: E402
from scripts.build_curated_cafe_catalog import (  # noqa: E402
    DEFAULT_OUTPUT as DEFAULT_CURATED_CACHE,
)
from scripts.cache_kakao_cafes import (  # noqa: E402
    DEFAULT_CACHE as DEFAULT_KAKAO_CACHE,
    MANIFEST_SCHEMA_VERSION as KAKAO_MANIFEST_SCHEMA_VERSION,
    manifest_path_for as kakao_manifest_path_for,
)
from scripts.cache_refreshment_candidates import (  # noqa: E402
    DEFAULT_CACHE as DEFAULT_PERMIT_CACHE,
    read_candidate_cache,
)


DEFAULT_OUTPUT = (
    BACKEND_DIR / "data" / f"provider-seoul-cafes-{OVERTURE_RELEASE}.jsonl"
)


def _read_kakao_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"invalid or missing Kakao cache manifest: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("Kakao cache manifest must be a JSON object")
    return payload


def _validate_kakao_manifest(
    manifest: dict[str, object],
    *,
    cache_bytes: bytes,
    record_count: int,
) -> None:
    if manifest.get("complete") is not True:
        raise ValueError("Kakao cache manifest is incomplete")
    schema_version = manifest.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != KAKAO_MANIFEST_SCHEMA_VERSION
    ):
        raise ValueError("unsupported Kakao cache manifest schema_version")
    if manifest.get("category_group_code") != KAKAO_CAFE_CATEGORY_CODE:
        raise ValueError("Kakao cache manifest category_group_code mismatch")
    if manifest.get("bbox") != list(SEOUL_BBOX):
        raise ValueError("Kakao cache manifest bbox does not cover configured Seoul")
    unresolved_count = manifest.get("unresolved_count")
    if type(unresolved_count) is not int or unresolved_count != 0:
        raise ValueError("Kakao cache manifest contains unresolved cells")

    expected_count = manifest.get("record_count")
    if type(expected_count) is not int or expected_count < 0:
        raise ValueError("Kakao cache manifest record_count is invalid")
    if expected_count != record_count:
        raise ValueError("Kakao cache record_count does not match manifest")

    expected_digest = manifest.get("cache_sha256")
    actual_digest = hashlib.sha256(cache_bytes).hexdigest()
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or not hmac.compare_digest(expected_digest, actual_digest)
    ):
        raise ValueError("Kakao cache sha256 does not match manifest")


def read_kakao_cache(
    path: Path,
    manifest_path: Path,
) -> tuple[KakaoPlace, ...]:
    """Read one manifest-verified Kakao JSONL cache without network access."""

    manifest = _read_kakao_manifest(manifest_path)
    try:
        cache_bytes = path.read_bytes()
        cache_text = cache_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid or missing Kakao cache: {path}") from exc
    records: list[KakaoPlace] = []
    for line_number, line in enumerate(cache_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("record must be a JSON object")
            records.append(KakaoPlace.model_validate(payload))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid Kakao cache line {line_number} ({type(exc).__name__})"
            ) from exc
    _validate_kakao_manifest(
        manifest,
        cache_bytes=cache_bytes,
        record_count=len(records),
    )
    return tuple(records)


def _write_temp(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def publish_build(
    output: Path,
    manifest: Path,
    build: ProviderCatalogBuild,
) -> None:
    """Atomically create catalog and aggregate manifest; never overwrite."""

    if output == manifest:
        raise ValueError("output and manifest paths must differ")
    for path in (output, manifest):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {path}")
    cache_bytes = serialize_provider_catalog(build)
    manifest_bytes = serialize_provider_catalog_manifest(build, cache_bytes)
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
    parser.add_argument(
        "--curated-cache", type=Path, default=DEFAULT_CURATED_CACHE
    )
    parser.add_argument("--permit-cache", type=Path, default=DEFAULT_PERMIT_CACHE)
    parser.add_argument("--kakao-cache", type=Path, default=DEFAULT_KAKAO_CACHE)
    parser.add_argument(
        "--kakao-manifest",
        type=Path,
        help="Kakao cache manifest (default: <kakao-cache>.manifest.json)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = args.manifest or args.output.with_suffix(".manifest.json")
    kakao_manifest = args.kakao_manifest or kakao_manifest_path_for(
        args.kakao_cache
    )
    for path in (args.output, manifest):
        if path.exists():
            print(
                f"build failed: refusing to overwrite existing output: {path}",
                file=sys.stderr,
            )
            return 1
    try:
        build = build_provider_cafe_catalog(
            tuple(iter_curated_records(args.curated_cache)),
            read_candidate_cache(args.permit_cache),
            read_kakao_cache(args.kakao_cache, kakao_manifest),
        )
        publish_build(args.output, manifest, build)
    except Exception as exc:
        print(f"build failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(f"provider catalog created: {args.output}")
    print(
        "provider_refs/new_cafes: "
        f"{len(build.existing_provider_refs)}/"
        f"{len(build.new_cafe_candidates)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
