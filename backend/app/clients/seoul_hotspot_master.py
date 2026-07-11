"""Client for official Seoul major-place master file attachments."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import Message

import httpx

from app.config import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    SEOUL_DATAFILE_DOWNLOAD_URL,
    SEOUL_HOTSPOT_MASTER_INF_ID,
    SEOUL_HOTSPOT_MASTER_INF_SEQ,
)


ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class SeoulMasterDownloadError(RuntimeError):
    """Raised when the master attachment cannot be safely downloaded."""


@dataclass(frozen=True, slots=True)
class DownloadedAttachment:
    filename: str
    content: bytes


def _attachment_filename(header: str | None) -> str:
    if not header:
        raise SeoulMasterDownloadError("response has no Content-Disposition")

    message = Message()
    message["Content-Disposition"] = header
    if message.get_content_disposition() != "attachment":
        raise SeoulMasterDownloadError(
            "Content-Disposition must identify an attachment"
        )
    filename = message.get_filename()
    if not filename:
        raise SeoulMasterDownloadError(
            "Content-Disposition attachment has no filename"
        )
    return filename


def _validate_attachment(
    response: httpx.Response, *, expected_suffix: str
) -> DownloadedAttachment:
    filename = _attachment_filename(response.headers.get("Content-Disposition"))
    if not filename.lower().endswith(expected_suffix.lower()):
        raise SeoulMasterDownloadError(
            f"attachment filename does not end with {expected_suffix}"
        )
    content = response.content
    if not content.startswith(ZIP_SIGNATURES):
        raise SeoulMasterDownloadError(
            "attachment is not a ZIP-container file (invalid magic bytes)"
        )
    return DownloadedAttachment(filename=filename, content=content)


class SeoulHotspotMasterClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._transport = transport

    def download(
        self, *, sequence: int, expected_suffix: str
    ) -> DownloadedAttachment:
        if sequence < 1:
            raise ValueError("sequence must be positive")
        if expected_suffix.lower() not in {".xlsx", ".zip"}:
            raise ValueError("expected_suffix must be .xlsx or .zip")

        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    HTTP_TIMEOUT_SECONDS, connect=HTTP_CONNECT_TIMEOUT_SECONDS
                ),
                headers={"User-Agent": HTTP_USER_AGENT},
                transport=self._transport,
                follow_redirects=True,
            ) as client:
                response = client.post(
                    SEOUL_DATAFILE_DOWNLOAD_URL,
                    params={"useCache": "false"},
                    data={
                        "infId": SEOUL_HOTSPOT_MASTER_INF_ID,
                        "infSeq": SEOUL_HOTSPOT_MASTER_INF_SEQ,
                        "seq": sequence,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SeoulMasterDownloadError(
                f"Seoul datafile server returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise SeoulMasterDownloadError(
                f"Seoul datafile request failed ({type(exc).__name__})"
            ) from None

        return _validate_attachment(response, expected_suffix=expected_suffix)
