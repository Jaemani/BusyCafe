from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.clients.seoul_living_population_files import (
    DownloadTarget,
    SeoulLivingPopulationFilesClient,
    SeoulLivingPopulationFilesError,
    build_download_target,
)
from scripts import download_living_population


ZIP_BYTES = b"PK\x03\x04mock living population zip"


def test_builds_daily_target_with_confirmed_seq_rule() -> None:
    target = build_download_target(date="20260708")
    assert target.seq == "260708"
    assert target.expected_filename == "250_LOCAL_RESD_20260708.zip"


def test_builds_monthly_target_with_confirmed_seq_rule() -> None:
    target = build_download_target(month="202606")
    assert target.seq == "2606"
    assert target.expected_filename == "250_LOCAL_RESD_202606.zip"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"date": "20260708", "month": "202606"},
        {"date": "2026078"},
        {"date": "2026070a"},
        {"date": "19990708"},
        {"date": "20261308"},
        {"month": "20266"},
        {"month": "202613"},
        {"month": "199906"},
    ],
)
def test_rejects_invalid_periods(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        build_download_target(**kwargs)


def _attachment_response(
    filename: str = "250_LOCAL_RESD_20260708.zip", content: bytes = ZIP_BYTES
) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        content=content,
    )


def test_client_posts_confirmed_bulk_file_form(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.host == "datafile.seoul.go.kr"
        assert request.url.params["useCache"] == "false"
        assert parse_qs(request.content.decode()) == {
            "infId": ["OA-22784"],
            "infSeq": ["1"],
            "seq": ["260708"],
        }
        return _attachment_response()

    client = SeoulLivingPopulationFilesClient(
        transport=httpx.MockTransport(handler)
    )
    part = tmp_path / "out.zip.part"
    info = client.download_to(build_download_target(date="20260708"), part)

    assert part.read_bytes() == ZIP_BYTES
    assert info.size_bytes == len(ZIP_BYTES)
    assert info.filename == "250_LOCAL_RESD_20260708.zip"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=ZIP_BYTES), "Content-Disposition"),
        (
            _attachment_response(filename="250_LOCAL_RESD_20260709.zip"),
            "does not match expected",
        ),
        (
            _attachment_response(content=b"<html>error page</html>"),
            "magic bytes",
        ),
        (_attachment_response(content=b""), "empty"),
        (httpx.Response(500, content=b"boom"), "HTTP 500"),
    ],
)
def test_client_rejects_bad_responses_and_removes_partial(
    tmp_path: Path, response: httpx.Response, message: str
) -> None:
    client = SeoulLivingPopulationFilesClient(
        transport=httpx.MockTransport(lambda _: response)
    )
    part = tmp_path / "out.zip.part"

    with pytest.raises(SeoulLivingPopulationFilesError, match=message):
        client.download_to(build_download_target(date="20260708"), part)
    assert not part.exists()


def test_script_dry_run_makes_no_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        download_living_population, "LIVING_POPULATION_DATA_DIR", tmp_path
    )

    class ExplodingClient:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("dry-run must not construct a client")

    monkeypatch.setattr(
        download_living_population,
        "SeoulLivingPopulationFilesClient",
        ExplodingClient,
    )

    assert download_living_population.main(["--date", "20260708"]) == 0
    out = capsys.readouterr().out
    assert "seq=260708" in out
    assert "dry-run" in out


def test_script_apply_publishes_atomically_and_cleans_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population, "LIVING_POPULATION_DATA_DIR", tmp_path
    )

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def download_to(self, target: DownloadTarget, part: Path):
            part.write_bytes(ZIP_BYTES)
            from app.clients.seoul_living_population_files import (
                DownloadedFileInfo,
            )

            return DownloadedFileInfo(
                filename=target.expected_filename,
                size_bytes=len(ZIP_BYTES),
                sha256="0" * 64,
            )

    monkeypatch.setattr(
        download_living_population, "SeoulLivingPopulationFilesClient", FakeClient
    )

    assert download_living_population.main(["--date", "20260708", "--apply"]) == 0
    destination = tmp_path / "250_LOCAL_RESD_20260708.zip"
    assert destination.read_bytes() == ZIP_BYTES
    assert not destination.with_name(destination.name + ".part").exists()


def test_script_refuses_existing_destination_before_any_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        download_living_population, "LIVING_POPULATION_DATA_DIR", tmp_path
    )
    existing = tmp_path / "250_LOCAL_RESD_20260708.zip"
    existing.write_bytes(ZIP_BYTES)

    class ExplodingClient:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("must not download when destination exists")

    monkeypatch.setattr(
        download_living_population,
        "SeoulLivingPopulationFilesClient",
        ExplodingClient,
    )

    assert download_living_population.main(["--date", "20260708", "--apply"]) == 1
    assert "refusing to overwrite" in capsys.readouterr().out
    assert existing.read_bytes() == ZIP_BYTES


def test_script_apply_failure_leaves_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population, "LIVING_POPULATION_DATA_DIR", tmp_path
    )

    class FailingClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def download_to(self, target: DownloadTarget, part: Path):
            part.write_bytes(b"partial")
            raise SeoulLivingPopulationFilesError("simulated network failure")

    monkeypatch.setattr(
        download_living_population, "SeoulLivingPopulationFilesClient", FailingClient
    )

    assert download_living_population.main(["--date", "20260708", "--apply"]) == 1
    assert list(tmp_path.iterdir()) == []
