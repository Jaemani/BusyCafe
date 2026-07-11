from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from scripts import verify_apis


def test_preflight_reports_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verify_apis,
        "get_settings",
        lambda: SimpleNamespace(seoul_api_key=None, kakao_rest_key=None),
    )
    with pytest.raises(RuntimeError, match="KAKAO_REST_KEY"):
        verify_apis._preflight(["kakao"])


def test_atomic_create_never_overwrites(tmp_path: Path) -> None:
    destination = tmp_path / "fixture.json"
    verify_apis._atomic_create_json(destination, {"version": 1})
    with pytest.raises(FileExistsError):
        verify_apis._atomic_create_json(destination, {"version": 2})
    assert '"version": 1' in destination.read_text(encoding="utf-8")


def test_summary_uses_measured_flat_seoul_fixture() -> None:
    fixture_path = verify_apis.FIXTURE_FILES["seoul"]
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    summary = verify_apis._summary({"seoul": payload})

    assert summary.seoul_area_name == "광화문광장"
    assert summary.seoul_area_code == "POI088"
    assert summary.observed_seoul_labels == ["보통", "여유"]


def test_main_preserves_raw_fixture_when_provisional_parsing_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture = tmp_path / "citydata_sample.json"
    summary = tmp_path / "citydata_sample.summary.json"
    validation_error = tmp_path / "citydata_sample.validation_error.txt"
    monkeypatch.setattr(verify_apis, "FIXTURE_FILES", {"seoul": fixture})
    monkeypatch.setattr(verify_apis, "SUMMARY_FILES", {"seoul": summary})
    monkeypatch.setattr(
        verify_apis, "VALIDATION_ERROR_FILES", {"seoul": validation_error}
    )
    monkeypatch.setattr(verify_apis, "_parse_args", lambda: type("Args", (), {"service": "seoul"})())
    monkeypatch.setattr(verify_apis, "_preflight", lambda _: {"seoul": "key"})
    monkeypatch.setattr(
        verify_apis, "_fetch_service", lambda *_: {"unexpected_upstream": True}
    )

    assert verify_apis.main() == 3
    assert json.loads(fixture.read_text(encoding="utf-8")) == {
        "unexpected_upstream": True
    }
    assert validation_error.exists()
    assert not summary.exists()
