#!/usr/bin/env python3
"""Create an immutable, local OA-16095 cafe-candidate cache; never write DB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from math import ceil
from pathlib import Path
from tempfile import NamedTemporaryFile

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.seoul_refreshment_permits import (  # noqa: E402
    SeoulRefreshmentPermitClient,
)
from app.config import (  # noqa: E402
    SEOUL_REFRESHMENT_PERMIT_DATASET_ID,
    SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE,
    SEOUL_REFRESHMENT_PERMIT_SERVICE,
    get_settings,
)
from app.ingest.seoul_refreshment_candidates import (  # noqa: E402
    CandidateResolution,
    PlaceCandidate,
    resolve_permit_candidates,
    select_review_sample,
)
from app.schemas import SeoulRefreshmentPermitPage  # noqa: E402


DEFAULT_CACHE = BACKEND_DIR / "data" / "seoul_refreshment_candidates.jsonl"


class CandidateCacheError(RuntimeError):
    """Raised when the source cannot produce one complete immutable cache."""


def fetch_candidate_resolution(
    fetch_page: Callable[[int, int], SeoulRefreshmentPermitPage],
    *,
    page_size: int = SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE,
) -> CandidateResolution:
    """Fetch one stable sequential source view and run the pure resolver."""

    if page_size < 1 or page_size > SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE:
        raise ValueError("page_size is outside the verified API bounds")
    first = fetch_page(1, page_size)
    total_count = first.total_count
    page_count = max(1, ceil(total_count / page_size))
    rows = []
    for page_number in range(1, page_count + 1):
        start = (page_number - 1) * page_size + 1
        end = min(page_number * page_size, total_count)
        expected_rows = max(0, end - start + 1)
        page = first if page_number == 1 else fetch_page(start, end)
        if page.total_count != total_count:
            raise CandidateCacheError(
                f"source total changed during cache: {total_count} -> {page.total_count}"
            )
        if len(page.rows) != expected_rows:
            raise CandidateCacheError(
                f"page {page_number} returned {len(page.rows)} rows, "
                f"expected {expected_rows}"
            )
        rows.extend(page.rows)
    if len(rows) != total_count:
        raise CandidateCacheError(
            f"cache completeness mismatch: rows={len(rows)}, total={total_count}"
        )
    return resolve_permit_candidates(rows)


def _candidate_line(candidate: PlaceCandidate) -> str:
    return json.dumps(
        asdict(candidate), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def serialize_candidates(candidates: Sequence[PlaceCandidate]) -> bytes:
    lines = [_candidate_line(candidate) for candidate in candidates]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def build_manifest(resolution: CandidateResolution, cache_bytes: bytes) -> dict[str, object]:
    """Return aggregate facts only; no business names, addresses, phones, or IDs."""

    area_eligible_count = sum(
        candidate.facility_area_status == "eligible"
        for candidate in resolution.candidates
    )
    area_nonpositive_count = sum(
        candidate.facility_area_status == "nonpositive"
        for candidate in resolution.candidates
    )
    # Old rows have no status; count them with blank/nonnumeric evidence as
    # missing rather than making aggregate counts silently stop balancing.
    area_missing_count = (
        len(resolution.candidates) - area_eligible_count - area_nonpositive_count
    )
    return {
        "dataset_id": SEOUL_REFRESHMENT_PERMIT_DATASET_ID,
        "service": SEOUL_REFRESHMENT_PERMIT_SERVICE,
        "cache_sha256": hashlib.sha256(cache_bytes).hexdigest(),
        "candidate_count": len(resolution.candidates),
        "source_row_count": resolution.source_row_count,
        "unique_management_number_count": resolution.unique_management_number_count,
        "exact_duplicate_row_count": resolution.exact_duplicate_row_count,
        "phone_variant_group_count": resolution.phone_variant_group_count,
        "phone_conflict_group_count": resolution.phone_conflict_group_count,
        "quarantined_group_count": resolution.quarantined_group_count,
        "quarantine_reason_counts": resolution.quarantine_reason_counts,
        "exclusion_reason_counts": resolution.exclusion_reason_counts,
        "candidate_category_counts": resolution.candidate_category_counts,
        "facility_area_eligible_count": area_eligible_count,
        "facility_area_missing_count": area_missing_count,
        "facility_area_nonpositive_count": area_nonpositive_count,
    }


def _write_temp(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def publish_cache(
    cache_path: Path,
    manifest_path: Path,
    resolution: CandidateResolution,
) -> None:
    """Atomically create cache and aggregate manifest, refusing overwrites."""

    if cache_path == manifest_path:
        raise ValueError("cache and manifest paths must differ")
    for path in (cache_path, manifest_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {path}")
    cache_bytes = serialize_candidates(resolution.candidates)
    manifest_bytes = (
        json.dumps(
            build_manifest(resolution, cache_bytes),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    cache_temp = _write_temp(cache_path, cache_bytes)
    manifest_temp = _write_temp(manifest_path, manifest_bytes)
    cache_published = False
    try:
        os.link(cache_temp, cache_path)
        cache_published = True
        os.link(manifest_temp, manifest_path)
    except BaseException:
        if cache_published:
            cache_path.unlink(missing_ok=True)
        raise
    finally:
        cache_temp.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)


def read_candidate_cache(path: Path) -> tuple[PlaceCandidate, ...]:
    candidates: list[PlaceCandidate] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                candidates.append(PlaceCandidate(**json.loads(line)))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CandidateCacheError(
                    f"invalid candidate cache line {line_number} ({type(exc).__name__})"
                ) from exc
    return tuple(candidates)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--review-sample",
        type=int,
        metavar="N",
        help="read local --output only; print deterministic JSONL sample to stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest_path = args.manifest or args.output.with_suffix(".manifest.json")
    if args.review_sample is not None:
        if args.review_sample < 0:
            raise SystemExit("--review-sample must be >= 0")
        try:
            sample = select_review_sample(
                read_candidate_cache(args.output), args.review_sample
            )
        except Exception as exc:
            print(f"sample failed ({type(exc).__name__}): {exc}", file=sys.stderr)
            return 1
        for candidate in sample:
            print(_candidate_line(candidate))
        return 0

    for path in (args.output, manifest_path):
        if path.exists():
            print(f"cache failed: refusing to overwrite existing output: {path}", file=sys.stderr)
            return 1
    secret = get_settings().seoul_api_key
    if secret is None or not secret.get_secret_value().strip():
        print("cache failed: missing SEOUL_API_KEY", file=sys.stderr)
        return 2
    try:
        with SeoulRefreshmentPermitClient(secret.get_secret_value()) as client:
            resolution = fetch_candidate_resolution(client.fetch_page)
        publish_cache(args.output, manifest_path, resolution)
    except Exception as exc:
        print(f"cache failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(f"candidate cache created: {args.output}")
    print(
        f"candidates={len(resolution.candidates)} "
        f"quarantined={resolution.quarantined_group_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
