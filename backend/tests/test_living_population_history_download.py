from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.clients.seoul_living_population_files import (
    DownloadedFileInfo,
    SeoulLivingPopulationFilesError,
)
from scripts import download_living_population_history


def _write_zip(path: Path, content: bytes = b"fixture") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("250_LOCAL_RESD.csv", content)


def _write_prior_manifest(
    directory: Path,
    *,
    start: str,
    end: str,
    digest_overrides: dict[str, str] | None = None,
) -> Path:
    digest_overrides = digest_overrides or {}
    files = []
    for month in download_living_population_history.inclusive_months(start, end):
        filename = f"250_LOCAL_RESD_{month}.zip"
        path = directory / filename
        if path.exists():
            sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            status = "downloaded"
            size_bytes = path.stat().st_size
        else:
            sha256 = None
            status = "planned"
            size_bytes = None
        files.append(
            {
                "month": month,
                "seq": month[2:],
                "filename": filename,
                "path": str(path),
                "status": status,
                "size_bytes": size_bytes,
                "sha256": digest_overrides.get(month, sha256),
                "error": None,
            }
        )
    manifest = {
        "schema_version": 1,
        "dataset": "OA-22784",
        "start_month": start,
        "end_month": end,
        "mode": "apply",
        "resume": False,
        "status": "failed",
        "files": files,
    }
    path = directory / "backfill_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_inclusive_month_range_handles_year_boundary() -> None:
    assert download_living_population_history.inclusive_months(
        "202311", "202402"
    ) == ["202311", "202312", "202401", "202402"]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("20231", "202302"),
        ("202313", "202402"),
        ("210001", "210002"),
        ("202212", "202301"),
        ("202402", "202401"),
    ],
)
def test_rejects_invalid_or_unsupported_ranges(start: str, end: str) -> None:
    with pytest.raises(ValueError):
        download_living_population_history.inclusive_months(start, end)


def test_dry_run_prints_manifest_without_network_or_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )

    class ExplodingClient:
        def __init__(self) -> None:
            raise AssertionError("dry-run must not construct a client")

    monkeypatch.setattr(
        download_living_population_history,
        "SeoulLivingPopulationFilesClient",
        ExplodingClient,
    )
    result = download_living_population_history.main(
        ["--start-month", "202301", "--end-month", "202302"]
    )

    assert result == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["status"] == "planned"
    assert [item["status"] for item in manifest["files"]] == [
        "planned",
        "planned",
    ]
    assert list(tmp_path.iterdir()) == []


def test_apply_downloads_sequentially_and_writes_complete_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    calls: list[str] = []

    class FakeClient:
        def download_to(self, target, part: Path) -> DownloadedFileInfo:
            calls.append(target.seq)
            _write_zip(part, target.seq.encode())
            return DownloadedFileInfo(
                filename=target.expected_filename,
                size_bytes=part.stat().st_size,
                sha256="a" * 64,
            )

    monkeypatch.setattr(
        download_living_population_history,
        "SeoulLivingPopulationFilesClient",
        FakeClient,
    )
    result = download_living_population_history.main(
        [
            "--start-month",
            "202312",
            "--end-month",
            "202402",
            "--apply",
        ]
    )

    assert result == 0
    assert calls == ["2312", "2401", "2402"]
    manifest = json.loads((tmp_path / "backfill_manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert [item["status"] for item in manifest["files"]] == [
        "downloaded",
        "downloaded",
        "downloaded",
    ]
    assert not list(tmp_path.glob("*.part"))


def test_apply_preflight_refuses_existing_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    existing = tmp_path / "250_LOCAL_RESD_202302.zip"
    _write_zip(existing)

    class ExplodingClient:
        def __init__(self) -> None:
            raise AssertionError("preflight must finish before network")

    monkeypatch.setattr(
        download_living_population_history,
        "SeoulLivingPopulationFilesClient",
        ExplodingClient,
    )
    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202301",
                "--end-month",
                "202302",
                "--apply",
            ]
        )
        == 1
    )
    assert not (tmp_path / "250_LOCAL_RESD_202301.zip").exists()
    assert not (tmp_path / "backfill_manifest.json").exists()


def test_resume_verifies_and_skips_existing_then_downloads_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    existing = tmp_path / "250_LOCAL_RESD_202301.zip"
    _write_zip(existing)
    _write_prior_manifest(tmp_path, start="202301", end="202302")
    calls: list[str] = []

    class FakeClient:
        def download_to(self, target, part: Path) -> DownloadedFileInfo:
            calls.append(target.seq)
            _write_zip(part)
            return DownloadedFileInfo(
                filename=target.expected_filename,
                size_bytes=part.stat().st_size,
                sha256="b" * 64,
            )

    monkeypatch.setattr(
        download_living_population_history,
        "SeoulLivingPopulationFilesClient",
        FakeClient,
    )
    result = download_living_population_history.main(
        [
            "--start-month",
            "202301",
            "--end-month",
            "202302",
            "--apply",
            "--resume",
        ]
    )

    assert result == 0
    assert calls == ["2302"]
    manifest = json.loads((tmp_path / "backfill_manifest.json").read_text())
    first, second = manifest["files"]
    assert first["status"] == "skipped_verified"
    assert first["size_bytes"] == existing.stat().st_size
    assert len(first["sha256"]) == 64
    assert second["status"] == "downloaded"


def test_resume_rejects_invalid_existing_archive_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    (tmp_path / "250_LOCAL_RESD_202301.zip").write_bytes(b"not a zip")
    manifest_path = _write_prior_manifest(
        tmp_path, start="202301", end="202301"
    )
    original_manifest = manifest_path.read_bytes()

    class ExplodingClient:
        def __init__(self) -> None:
            raise AssertionError("invalid resume file must block network")

    monkeypatch.setattr(
        download_living_population_history,
        "SeoulLivingPopulationFilesClient",
        ExplodingClient,
    )
    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202301",
                "--end-month",
                "202301",
                "--apply",
                "--resume",
            ]
        )
        == 1
    )
    assert manifest_path.read_bytes() == original_manifest


def test_resume_rejects_existing_file_without_prior_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    _write_zip(tmp_path / "250_LOCAL_RESD_202301.zip")

    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202301",
                "--end-month",
                "202301",
                "--apply",
                "--resume",
            ]
        )
        == 1
    )
    assert not (tmp_path / "backfill_manifest.json").exists()


def test_resume_rejects_digest_mismatch_and_preserves_prior_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    _write_zip(tmp_path / "250_LOCAL_RESD_202301.zip")
    manifest_path = _write_prior_manifest(
        tmp_path,
        start="202301",
        end="202301",
        digest_overrides={"202301": "0" * 64},
    )
    original_manifest = manifest_path.read_bytes()

    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202301",
                "--end-month",
                "202301",
                "--apply",
                "--resume",
            ]
        )
        == 1
    )
    assert manifest_path.read_bytes() == original_manifest


def test_apply_refuses_existing_manifest_without_resume_and_preserves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    manifest_path = _write_prior_manifest(
        tmp_path, start="202301", end="202301"
    )
    original_manifest = manifest_path.read_bytes()

    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202301",
                "--end-month",
                "202301",
                "--apply",
            ]
        )
        == 1
    )
    assert manifest_path.read_bytes() == original_manifest


def test_resume_refuses_different_range_manifest_and_preserves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    manifest_path = _write_prior_manifest(
        tmp_path, start="202301", end="202301"
    )
    original_manifest = manifest_path.read_bytes()

    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202302",
                "--end-month",
                "202302",
                "--apply",
                "--resume",
            ]
        )
        == 1
    )
    assert manifest_path.read_bytes() == original_manifest


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("schema_version", 2), ("dataset", "OA-WRONG")],
)
def test_resume_refuses_manifest_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    invalid_value: object,
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    manifest_path = _write_prior_manifest(
        tmp_path, start="202301", end="202301"
    )
    payload = json.loads(manifest_path.read_text())
    payload[field] = invalid_value
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    original_manifest = manifest_path.read_bytes()

    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202301",
                "--end-month",
                "202301",
                "--apply",
                "--resume",
            ]
        )
        == 1
    )
    assert manifest_path.read_bytes() == original_manifest


def test_resume_dry_run_validates_but_does_not_write_or_use_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    _write_zip(tmp_path / "250_LOCAL_RESD_202301.zip")
    manifest_path = _write_prior_manifest(
        tmp_path, start="202301", end="202301"
    )
    original_manifest = manifest_path.read_bytes()

    class ExplodingClient:
        def __init__(self) -> None:
            raise AssertionError("dry-run must not construct a client")

    monkeypatch.setattr(
        download_living_population_history,
        "SeoulLivingPopulationFilesClient",
        ExplodingClient,
    )
    assert (
        download_living_population_history.main(
            [
                "--start-month",
                "202301",
                "--end-month",
                "202301",
                "--resume",
            ]
        )
        == 0
    )
    assert manifest_path.read_bytes() == original_manifest


def test_apply_stops_after_failure_and_records_resumable_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        download_living_population_history,
        "LIVING_POPULATION_DATA_DIR",
        tmp_path,
    )
    calls: list[str] = []

    class FailingSecondClient:
        def download_to(self, target, part: Path) -> DownloadedFileInfo:
            calls.append(target.seq)
            if target.seq == "2302":
                part.write_bytes(b"partial")
                raise SeoulLivingPopulationFilesError("simulated failure")
            _write_zip(part)
            return DownloadedFileInfo(
                filename=target.expected_filename,
                size_bytes=part.stat().st_size,
                sha256="c" * 64,
            )

    monkeypatch.setattr(
        download_living_population_history,
        "SeoulLivingPopulationFilesClient",
        FailingSecondClient,
    )
    result = download_living_population_history.main(
        [
            "--start-month",
            "202301",
            "--end-month",
            "202303",
            "--apply",
        ]
    )

    assert result == 1
    assert calls == ["2301", "2302"]
    assert not list(tmp_path.glob("*.part"))
    manifest = json.loads((tmp_path / "backfill_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert [item["status"] for item in manifest["files"]] == [
        "downloaded",
        "failed",
        "planned",
    ]
