#!/usr/bin/env python3
"""Download the official OA-21285 hotspot master attachments.

Existing fixtures are never overwritten. Both output paths are checked before
any network request, and each completed response is atomically published.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.seoul_hotspot_master import (  # noqa: E402
    SeoulHotspotMasterClient,
    SeoulMasterDownloadError,
)
from app.config import (  # noqa: E402
    SEOUL_HOTSPOT_AREAS_PATH,
    SEOUL_HOTSPOT_AREAS_SEQ,
    SEOUL_HOTSPOT_LIST_PATH,
    SEOUL_HOTSPOT_LIST_SEQ,
)


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    name: str
    sequence: int
    expected_suffix: str
    output_path: Path


DOWNLOADS = {
    "list": DownloadSpec(
        name="list",
        sequence=SEOUL_HOTSPOT_LIST_SEQ,
        expected_suffix=".xlsx",
        output_path=SEOUL_HOTSPOT_LIST_PATH,
    ),
    "areas": DownloadSpec(
        name="areas",
        sequence=SEOUL_HOTSPOT_AREAS_SEQ,
        expected_suffix=".zip",
        output_path=SEOUL_HOTSPOT_AREAS_PATH,
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        choices=("all", *DOWNLOADS),
        default="all",
        help="attachment to download (default: all)",
    )
    return parser.parse_args()


def _selected(choice: str) -> list[DownloadSpec]:
    return list(DOWNLOADS.values()) if choice == "all" else [DOWNLOADS[choice]]


def _preflight(specs: list[DownloadSpec]) -> None:
    collisions = [spec.output_path for spec in specs if spec.output_path.exists()]
    if collisions:
        joined = "\n  - ".join(str(path) for path in collisions)
        raise RuntimeError(
            "refusing to overwrite existing hotspot master fixture(s):\n"
            f"  - {joined}"
        )


def _atomic_create_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        # A hard link publishes the completed inode atomically and fails if the
        # destination appeared after preflight, preserving no-overwrite safety.
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    specs = _selected(_parse_args().file)
    try:
        _preflight(specs)
        client = SeoulHotspotMasterClient()
        for spec in specs:
            attachment = client.download(
                sequence=spec.sequence,
                expected_suffix=spec.expected_suffix,
            )
            _atomic_create_bytes(spec.output_path, attachment.content)
            print(
                f"created {spec.output_path} "
                f"(upstream filename: {attachment.filename})"
            )
    except (OSError, RuntimeError, SeoulMasterDownloadError, ValueError) as exc:
        print(f"hotspot master download failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
