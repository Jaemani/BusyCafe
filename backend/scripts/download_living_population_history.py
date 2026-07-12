#!/usr/bin/env python3
"""Safely backfill monthly Seoul 250m living-population files (OA-22784).

The month range is inclusive. The command is a no-network dry-run unless
``--apply`` is explicit. Downloads are sequential, use the existing streaming
client and ``.part`` atomic-publication path, and never overwrite source files.
``--resume`` may skip an existing file only after checking its ZIP structure
and recording its size and SHA-256 in the run manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.seoul_living_population_files import (  # noqa: E402
    DownloadTarget,
    SeoulLivingPopulationFilesClient,
    SeoulLivingPopulationFilesError,
    build_download_target,
)
from app.config import (  # noqa: E402
    LIVING_POPULATION_BACKFILL_MANIFEST_FILENAME,
    LIVING_POPULATION_BACKFILL_MANIFEST_SCHEMA_VERSION,
    LIVING_POPULATION_DATA_DIR,
    LIVING_POPULATION_HASH_CHUNK_BYTES,
    LIVING_POPULATION_HISTORY_START_MONTH,
    SEOUL_LIVING_POPULATION_INF_ID,
)
from scripts.download_living_population import download_one  # noqa: E402


def _valid_month(value: str) -> tuple[int, int]:
    if len(value) != 6 or not value.isascii() or not value.isdigit():
        raise ValueError("month must be YYYYMM digits")
    year, month = int(value[:4]), int(value[4:])
    if not 2000 <= year <= 2099 or not 1 <= month <= 12:
        raise ValueError("month must be a valid 20xx calendar month")
    return year, month


def inclusive_months(start: str, end: str) -> list[str]:
    """Return an inclusive, ascending YYYYMM range after strict validation."""

    start_year, start_month = _valid_month(start)
    end_year, end_month = _valid_month(end)
    if start < LIVING_POPULATION_HISTORY_START_MONTH:
        raise ValueError(
            "start month predates the verified OA-22784 history boundary "
            f"({LIVING_POPULATION_HISTORY_START_MONTH})"
        )
    if (start_year, start_month) > (end_year, end_month):
        raise ValueError("start month must not be after end month")

    result: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return result


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(LIVING_POPULATION_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_existing(path: Path) -> tuple[int, str]:
    """Verify a resumable local file without trusting its filename alone."""

    if not path.is_file():
        raise RuntimeError(f"existing destination is not a regular file: {path}")
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"existing destination is not a valid ZIP: {path}")
    with zipfile.ZipFile(path) as archive:
        if not archive.infolist():
            raise RuntimeError(f"existing ZIP has no members: {path}")
    return path.stat().st_size, _hash_file(path)


def _manifest_entry(
    month: str, target: DownloadTarget, destination: Path
) -> dict[str, Any]:
    return {
        "month": month,
        "seq": target.seq,
        "filename": target.expected_filename,
        "path": str(destination),
        "status": "planned",
        "size_bytes": None,
        "sha256": None,
        "error": None,
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def _load_resume_entries(
    path: Path, *, start: str, end: str, months: list[str]
) -> dict[str, dict[str, Any]]:
    """Load and strictly validate the manifest that authorizes a resume."""

    if not path.is_file():
        raise RuntimeError("--resume requires an existing regular manifest")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"resume manifest is unreadable: {exc}") from None
    if not isinstance(payload, dict):
        raise RuntimeError("resume manifest root must be an object")
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"]
        != LIVING_POPULATION_BACKFILL_MANIFEST_SCHEMA_VERSION
    ):
        raise RuntimeError("resume manifest schema_version mismatch")
    expected_header = {
        "dataset": SEOUL_LIVING_POPULATION_INF_ID,
        "start_month": start,
        "end_month": end,
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            raise RuntimeError(
                f"resume manifest {key} mismatch: expected {expected!r}"
            )
    files = payload.get("files")
    if not isinstance(files, list):
        raise RuntimeError("resume manifest files must be an array")
    by_month: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("month"), str):
            raise RuntimeError("resume manifest contains an invalid file entry")
        month = item["month"]
        if month in by_month:
            raise RuntimeError(f"resume manifest has duplicate month {month}")
        by_month[month] = item
    if set(by_month) != set(months):
        raise RuntimeError("resume manifest file months do not match requested range")
    for month in months:
        target = build_download_target(month=month)
        item = by_month[month]
        if item.get("seq") != target.seq:
            raise RuntimeError(f"resume manifest seq mismatch for {month}")
        if item.get("filename") != target.expected_filename:
            raise RuntimeError(f"resume manifest filename mismatch for {month}")
        if item.get("status") not in {
            "planned",
            "downloaded",
            "skipped_verified",
            "failed",
        }:
            raise RuntimeError(f"resume manifest status is invalid for {month}")
    return by_month


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-month", required=True, help="inclusive YYYYMM")
    parser.add_argument("--end-month", required=True, help="inclusive YYYYMM")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually download sequentially (default: dry-run, no network)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="verify and skip existing source files instead of blocking",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest: dict[str, Any] | None = None
    manifest_path: Path | None = None
    manifest_write_allowed = False
    try:
        months = inclusive_months(args.start_month, args.end_month)
        entries: list[dict[str, Any]] = []
        for month in months:
            target = build_download_target(month=month)
            destination = LIVING_POPULATION_DATA_DIR / target.expected_filename
            entries.append(_manifest_entry(month, target, destination))

        manifest = {
            "schema_version": LIVING_POPULATION_BACKFILL_MANIFEST_SCHEMA_VERSION,
            "dataset": SEOUL_LIVING_POPULATION_INF_ID,
            "start_month": args.start_month,
            "end_month": args.end_month,
            "mode": "apply" if args.apply else "dry-run",
            "resume": args.resume,
            "status": "planned",
            "files": entries,
        }
        manifest_path = (
            LIVING_POPULATION_DATA_DIR
            / LIVING_POPULATION_BACKFILL_MANIFEST_FILENAME
        )
        prior_entries: dict[str, dict[str, Any]] | None = None
        if manifest_path.exists():
            if not args.resume:
                if args.apply:
                    raise RuntimeError(
                        "backfill manifest already exists; refusing to overwrite "
                        "without --resume"
                    )
            else:
                prior_entries = _load_resume_entries(
                    manifest_path,
                    start=args.start_month,
                    end=args.end_month,
                    months=months,
                )
        elif args.resume:
            raise RuntimeError("--resume requested but prior manifest is missing")

        # Preflight every destination before starting any network operation.
        for entry in entries:
            destination = Path(entry["path"])
            part = destination.with_name(destination.name + ".part")
            if part.exists():
                entry["status"] = "blocked_partial"
                entry["error"] = f"stale partial file exists: {part}"
                manifest["status"] = "blocked"
                raise RuntimeError(entry["error"])
            if not destination.exists():
                continue
            if not args.resume:
                entry["status"] = "blocked_existing"
                entry["error"] = "existing destination; overwrite refused"
                manifest["status"] = "blocked"
                raise RuntimeError(
                    f"existing destination; use --resume to verify and skip: {destination}"
                )
            assert prior_entries is not None
            prior = prior_entries[entry["month"]]
            if prior.get("status") not in {"downloaded", "skipped_verified"}:
                raise RuntimeError(
                    f"existing {destination.name} has no successful prior manifest entry"
                )
            prior_size = prior.get("size_bytes")
            prior_sha256 = prior.get("sha256")
            if (
                type(prior_size) is not int
                or prior_size <= 0
                or not isinstance(prior_sha256, str)
                or len(prior_sha256) != 64
                or any(char not in "0123456789abcdef" for char in prior_sha256)
            ):
                raise RuntimeError(
                    f"prior manifest has invalid digest metadata for {entry['month']}"
                )
            size, sha256 = _verify_existing(destination)
            if size != prior_size or sha256 != prior_sha256:
                raise RuntimeError(
                    f"existing {destination.name} does not match prior manifest digest"
                )
            entry.update(
                status="skipped_verified", size_bytes=size, sha256=sha256
            )

        if prior_entries is not None:
            for entry in entries:
                if entry["status"] == "skipped_verified":
                    continue
                prior = prior_entries[entry["month"]]
                if prior.get("status") in {"downloaded", "skipped_verified"}:
                    raise RuntimeError(
                        f"prior manifest says {entry['filename']} succeeded but file is missing"
                    )

        # Only a fully successful preflight authorizes creating or replacing
        # the manifest. A mismatch must preserve the prior audit evidence.
        manifest_write_allowed = args.apply

        if not args.apply:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            print("dry-run: pass --apply to download", file=sys.stderr)
            return 0

        manifest["status"] = "in_progress"
        _write_manifest(manifest_path, manifest)
        client: SeoulLivingPopulationFilesClient | None = None
        for entry in entries:
            if entry["status"] == "skipped_verified":
                continue
            if client is None:
                client = SeoulLivingPopulationFilesClient()
            target = build_download_target(month=entry["month"])
            destination = Path(entry["path"])
            try:
                info = download_one(client, target, destination)
                entry.update(
                    status="downloaded",
                    size_bytes=info.size_bytes,
                    sha256=info.sha256,
                )
            except Exception as exc:
                entry["status"] = "failed"
                entry["error"] = str(exc)
                manifest["status"] = "failed"
                _write_manifest(manifest_path, manifest)
                raise
            _write_manifest(manifest_path, manifest)

        manifest["status"] = "complete"
        _write_manifest(manifest_path, manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"manifest: {manifest_path}", file=sys.stderr)
        return 0
    except (OSError, RuntimeError, SeoulLivingPopulationFilesError, ValueError) as exc:
        if manifest is not None:
            if manifest["status"] not in {"blocked", "failed"}:
                manifest["status"] = "failed"
            if manifest_write_allowed and manifest_path is not None:
                try:
                    _write_manifest(manifest_path, manifest)
                except OSError as manifest_exc:
                    print(
                        f"manifest write also failed: {manifest_exc}",
                        file=sys.stderr,
                    )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"living population history download failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
