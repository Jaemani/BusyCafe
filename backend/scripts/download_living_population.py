#!/usr/bin/env python3
"""Download one Seoul 250m living-population bulk file (OA-22784).

Dry-run by default: prints the derived portal request, the destination path
and whether it already exists, without any network access. ``--apply``
streams the file to ``<name>.part`` and publishes it atomically; existing
destinations are never overwritten. Files land under
``backend/data/living_population/`` (gitignored — these are hundreds of MB).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.seoul_living_population_files import (  # noqa: E402
    DownloadedFileInfo,
    DownloadTarget,
    SeoulLivingPopulationFilesClient,
    SeoulLivingPopulationFilesError,
    build_download_target,
)
from app.config import LIVING_POPULATION_DATA_DIR  # noqa: E402


def download_one(
    client: SeoulLivingPopulationFilesClient,
    target: DownloadTarget,
    destination: Path,
) -> DownloadedFileInfo:
    """Download and atomically publish one target without overwriting files."""

    if destination.exists():
        raise RuntimeError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_name(destination.name + ".part")
    if part_path.exists():
        raise RuntimeError(
            f"stale partial file exists, inspect and remove: {part_path}"
        )
    try:
        info = client.download_to(target, part_path)
        # Fails if the destination appears after preflight, preserving the
        # no-overwrite contract without relying on a check-then-replace race.
        os.link(part_path, destination)
        return info
    finally:
        part_path.unlink(missing_ok=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    period = parser.add_mutually_exclusive_group(required=True)
    period.add_argument("--date", help="daily file, YYYYMMDD")
    period.add_argument("--month", help="monthly file, YYYYMM")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually download (default: dry-run, no network)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        target = build_download_target(date=args.date, month=args.month)
        destination = LIVING_POPULATION_DATA_DIR / target.expected_filename

        print(f"target: seq={target.seq} -> {target.expected_filename}")
        print(f"destination: {destination}")
        if destination.exists():
            print("destination already exists; refusing to overwrite")
            return 0 if not args.apply else 1
        if not args.apply:
            print("dry-run: pass --apply to download")
            return 0

        client = SeoulLivingPopulationFilesClient()
        info = download_one(client, target, destination)
        print(
            f"created {destination} ({info.size_bytes} bytes, "
            f"sha256 {info.sha256})"
        )
    except (
        OSError,
        RuntimeError,
        SeoulLivingPopulationFilesError,
        ValueError,
    ) as exc:
        print(f"living population download failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
