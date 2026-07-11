from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.clients.seoul_hotspot_master import (
    SeoulHotspotMasterClient,
    SeoulMasterDownloadError,
)
from scripts.download_hotspot_master import _atomic_create_bytes, _preflight


ZIP_BYTES = b"PK\x03\x04mock zip container"


def test_master_client_posts_official_attachment_form() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.host == "datafile.seoul.go.kr"
        assert request.url.path == "/bigfile/iot/inf/nio_download.do"
        assert request.url.params["useCache"] == "false"
        assert parse_qs(request.content.decode()) == {
            "infId": ["OA-21285"],
            "infSeq": ["2"],
            "seq": ["23"],
        }
        return httpx.Response(
            200,
            headers={
                "Content-Disposition": 'attachment; filename="hotspots.xlsx"'
            },
            content=ZIP_BYTES,
        )

    client = SeoulHotspotMasterClient(transport=httpx.MockTransport(handler))
    attachment = client.download(sequence=23, expected_suffix=".xlsx")

    assert attachment.filename == "hotspots.xlsx"
    assert attachment.content == ZIP_BYTES


@pytest.mark.parametrize(
    ("headers", "content", "message"),
    [
        ({}, ZIP_BYTES, "Content-Disposition"),
        (
            {"Content-Disposition": 'inline; filename="hotspots.xlsx"'},
            ZIP_BYTES,
            "attachment",
        ),
        (
            {"Content-Disposition": 'attachment; filename="hotspots.csv"'},
            ZIP_BYTES,
            "does not end",
        ),
        (
            {"Content-Disposition": 'attachment; filename="hotspots.xlsx"'},
            b"<html>server error</html>",
            "magic bytes",
        ),
    ],
)
def test_master_client_rejects_invalid_attachment(
    headers: dict[str, str], content: bytes, message: str
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, headers=headers, content=content)
    )
    client = SeoulHotspotMasterClient(transport=transport)

    with pytest.raises(SeoulMasterDownloadError, match=message):
        client.download(sequence=23, expected_suffix=".xlsx")


def test_atomic_create_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "master.xlsx"
    _atomic_create_bytes(target, ZIP_BYTES)

    with pytest.raises(FileExistsError):
        _atomic_create_bytes(target, b"replacement")
    assert target.read_bytes() == ZIP_BYTES


def test_preflight_checks_all_paths_before_download(tmp_path: Path) -> None:
    from scripts.download_hotspot_master import DownloadSpec

    existing = tmp_path / "areas.zip"
    existing.write_bytes(ZIP_BYTES)
    specs = [
        DownloadSpec("list", 23, ".xlsx", tmp_path / "list.xlsx"),
        DownloadSpec("areas", 24, ".zip", existing),
    ]

    with pytest.raises(RuntimeError, match="areas.zip"):
        _preflight(specs)
