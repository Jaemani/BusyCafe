"""Client for Seoul 250m living-population bulk file downloads (OA-22784).

The portal serves every bulk file from the same ``nio_download.do`` endpoint
used by the hotspot master client, but the per-file ``seq`` is not a stored
constant: the dataset page derives it from the file date. The daily rule was
confirmed on 2026-07-12 with a complete real download
(``250_LOCAL_RESD_20260708.zip`` = 15,037,162 bytes). The monthly filename,
sequence and 448,638,322-byte size were read from the portal page; the monthly
body has not yet been downloaded in full:

* daily  ``YYYYMMDD`` -> ``seq = YYMMDD``  (page JS: ``downloadFile('260708')``)
* monthly ``YYYYMM``  -> ``seq = YYMM``

Downloads stream to a caller-provided ``.part`` path so a partial file can
never be mistaken for a completed one; the script layer publishes it
atomically and refuses to overwrite existing files.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

import httpx

from app.config import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    SEOUL_DATAFILE_DOWNLOAD_URL,
    SEOUL_LIVING_POPULATION_INF_ID,
    SEOUL_LIVING_POPULATION_INF_SEQ,
)


ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class SeoulLivingPopulationFilesError(RuntimeError):
    """Raised when a bulk living-population file cannot be safely downloaded."""


@dataclass(frozen=True, slots=True)
class DownloadTarget:
    """One resolvable bulk file: portal ``seq`` plus the expected filename."""

    seq: str
    expected_filename: str


@dataclass(frozen=True, slots=True)
class DownloadedFileInfo:
    filename: str
    size_bytes: int
    sha256: str


def build_download_target(
    *, date: str | None = None, month: str | None = None
) -> DownloadTarget:
    """Derive the portal ``seq`` and expected filename for one bulk file."""

    if (date is None) == (month is None):
        raise ValueError("exactly one of date or month is required")
    if date is not None:
        if len(date) != 8 or not date.isascii() or not date.isdigit():
            raise ValueError("date must be YYYYMMDD digits")
        if not date.startswith("20"):
            raise ValueError("date must be a 20xx calendar date")
        if not 1 <= int(date[4:6]) <= 12 or not 1 <= int(date[6:8]) <= 31:
            raise ValueError("date must be a plausible calendar date")
        return DownloadTarget(
            seq=date[2:], expected_filename=f"250_LOCAL_RESD_{date}.zip"
        )
    assert month is not None
    if len(month) != 6 or not month.isascii() or not month.isdigit():
        raise ValueError("month must be YYYYMM digits")
    if not month.startswith("20") or not 1 <= int(month[4:6]) <= 12:
        raise ValueError("month must be a plausible 20xx month")
    return DownloadTarget(
        seq=month[2:], expected_filename=f"250_LOCAL_RESD_{month}.zip"
    )


def _attachment_filename(header: str | None) -> str:
    if not header:
        raise SeoulLivingPopulationFilesError(
            "response has no Content-Disposition"
        )
    message = Message()
    message["Content-Disposition"] = header
    if message.get_content_disposition() != "attachment":
        raise SeoulLivingPopulationFilesError(
            "Content-Disposition must identify an attachment"
        )
    filename = message.get_filename()
    if not filename:
        raise SeoulLivingPopulationFilesError(
            "Content-Disposition attachment has no filename"
        )
    return filename


class SeoulLivingPopulationFilesClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def download_to(
        self, target: DownloadTarget, destination_part: Path
    ) -> DownloadedFileInfo:
        """Stream one bulk file to ``destination_part`` and report its digest.

        Validates the upstream filename against the derived expectation and
        the ZIP magic bytes of the first chunk before anything is kept, and
        removes the partial file on every failure path.
        """

        digest = hashlib.sha256()
        size = 0
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT_SECONDS
                ),
                headers={"User-Agent": HTTP_USER_AGENT},
                transport=self._transport,
                follow_redirects=True,
            ) as client:
                with client.stream(
                    "POST",
                    SEOUL_DATAFILE_DOWNLOAD_URL,
                    params={"useCache": "false"},
                    data={
                        "infId": SEOUL_LIVING_POPULATION_INF_ID,
                        "infSeq": SEOUL_LIVING_POPULATION_INF_SEQ,
                        "seq": target.seq,
                    },
                ) as response:
                    response.raise_for_status()
                    filename = _attachment_filename(
                        response.headers.get("Content-Disposition")
                    )
                    if filename != target.expected_filename:
                        raise SeoulLivingPopulationFilesError(
                            "upstream filename "
                            f"{filename!r} does not match expected "
                            f"{target.expected_filename!r}"
                        )
                    with destination_part.open("wb") as output:
                        first = True
                        for chunk in response.iter_bytes():
                            if first and chunk:
                                if not chunk.startswith(ZIP_SIGNATURES):
                                    raise SeoulLivingPopulationFilesError(
                                        "response is not a ZIP file "
                                        "(invalid magic bytes)"
                                    )
                                first = False
                            digest.update(chunk)
                            size += len(chunk)
                            output.write(chunk)
                    if first:
                        raise SeoulLivingPopulationFilesError(
                            "response body was empty"
                        )
        except httpx.HTTPStatusError as exc:
            destination_part.unlink(missing_ok=True)
            raise SeoulLivingPopulationFilesError(
                f"Seoul datafile server returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            destination_part.unlink(missing_ok=True)
            raise SeoulLivingPopulationFilesError(
                f"Seoul datafile request failed ({type(exc).__name__})"
            ) from None
        except Exception:
            destination_part.unlink(missing_ok=True)
            raise

        return DownloadedFileInfo(
            filename=filename, size_bytes=size, sha256=digest.hexdigest()
        )
