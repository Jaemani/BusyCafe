from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.ingest.kakao_places import KakaoSweepReport, UnresolvedCell
from app.schemas import KakaoPlace
from scripts import cache_kakao_cafes


def _record(identifier: str, name: str = "테스트 카페") -> KakaoPlace:
    return KakaoPlace.model_validate(
        {
            "id": identifier,
            "place_name": name,
            "category_group_code": "CE7",
            "x": "127.0",
            "y": "37.5",
            "place_url": f"http://place.map.kakao.com/{identifier}",
        }
    )


def _report(
    *,
    records: tuple[KakaoPlace, ...] = (),
    unresolved: tuple[UnresolvedCell, ...] = (),
) -> KakaoSweepReport:
    return KakaoSweepReport(
        records=records,
        api_calls=5,
        http_attempts=6,
        source_documents=len(records),
        duplicate_documents=0,
        completed_leaf_cells=4,
        split_cells=1,
        max_depth_visited=1,
        unresolved=unresolved,
    )


def test_manifest_contains_aggregate_counts_and_canonical_cache_hash() -> None:
    report = _report(records=(_record("1", "비공개 이름"), _record("2")))
    manifest = cache_kakao_cafes.build_manifest(
        report,
        bbox=(126.0, 37.0, 127.0, 38.0),
        generated_at=datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert manifest["complete"] is True
    assert manifest["record_count"] == 2
    assert manifest["api_calls"] == 5
    assert manifest["http_attempts"] == 6
    assert "비공개 이름" not in json.dumps(manifest, ensure_ascii=False)
    expected = hashlib.sha256(
        b"".join(cache_kakao_cafes._record_line(record) for record in report.records)
    ).hexdigest()
    assert manifest["cache_sha256"] == expected


def test_publish_cache_atomically_writes_jsonl_and_manifest(tmp_path) -> None:
    records = (_record("1"), _record("2"))
    report = _report(records=records)
    manifest = cache_kakao_cafes.build_manifest(
        report,
        bbox=(126.0, 37.0, 127.0, 38.0),
        generated_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    output = tmp_path / "nested" / "cafes.jsonl"

    cache_path, manifest_path = cache_kakao_cafes.publish_cache(
        records, manifest, output
    )

    assert cache_path == output
    assert manifest_path == output.with_suffix(".jsonl.manifest.json")
    lines = output.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["1", "2"]
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert not list(output.parent.glob("*.part"))


def test_publish_refuses_incomplete_report_manifest(tmp_path) -> None:
    output = tmp_path / "cafes.jsonl"
    with pytest.raises(ValueError, match="incomplete"):
        cache_kakao_cafes.publish_cache(
            (), {"complete": False, "cache_sha256": hashlib.sha256().hexdigest()}, output
        )
    assert not output.exists()


class _FakeClient:
    def __init__(self, key: str) -> None:
        assert key == "secret"

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_main_defaults_to_dry_run_and_writes_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    output = tmp_path / "cafes.jsonl"
    report = _report(records=(_record("1"),))
    monkeypatch.setattr(
        cache_kakao_cafes,
        "get_settings",
        lambda: SimpleNamespace(kakao_rest_key=SecretStr("secret")),
    )
    monkeypatch.setattr(cache_kakao_cafes, "KakaoLocalClient", _FakeClient)
    monkeypatch.setattr(cache_kakao_cafes, "sweep_kakao_cafes", lambda *a, **k: report)

    assert cache_kakao_cafes.main(["--output", str(output)]) == 0

    assert not output.exists()
    assert not cache_kakao_cafes.manifest_path_for(output).exists()
    assert "dry-run: cache not written" in capsys.readouterr().out


def test_main_fail_closes_on_unresolved_cells_even_with_apply(
    tmp_path, monkeypatch, capsys
) -> None:
    output = tmp_path / "cafes.jsonl"
    unresolved = UnresolvedCell(
        path="root.ne",
        depth=1,
        rect=(126.5, 37.5, 127.0, 38.0),
        reason="max_depth_saturated",
        total_count=100,
    )
    report = _report(unresolved=(unresolved,))
    monkeypatch.setattr(
        cache_kakao_cafes,
        "get_settings",
        lambda: SimpleNamespace(kakao_rest_key=SecretStr("secret")),
    )
    monkeypatch.setattr(cache_kakao_cafes, "KakaoLocalClient", _FakeClient)
    monkeypatch.setattr(cache_kakao_cafes, "sweep_kakao_cafes", lambda *a, **k: report)

    assert (
        cache_kakao_cafes.main(["--output", str(output), "--apply"])
        == 2
    )

    assert not output.exists()
    captured = capsys.readouterr()
    assert "max_depth_saturated" in captured.err
    assert '"complete": false' in captured.out
