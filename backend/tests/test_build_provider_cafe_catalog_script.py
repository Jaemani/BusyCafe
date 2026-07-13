from __future__ import annotations

import hashlib
import json

import pytest

from app.config import KAKAO_CAFE_CATEGORY_CODE, SEOUL_BBOX
from app.ingest.overture_places import OvertureCafeRecord
from app.ingest.provider_cafe_catalog import build_provider_cafe_catalog
from app.schemas import KakaoPlace
from scripts.build_provider_cafe_catalog import (
    publish_build,
    read_kakao_cache,
)


def _kakao(identifier: str) -> KakaoPlace:
    return KakaoPlace.model_validate(
        {
            "id": identifier,
            "place_name": "테스트 카페",
            "category_group_code": "CE7",
            "x": "126.98",
            "y": "37.55",
            "place_url": f"http://place.map.kakao.com/{identifier}",
        }
    )


def _build():
    overture = OvertureCafeRecord(
        overture_id="ov-1",
        name="테스트 카페",
        lat=37.55,
        lng=126.98,
        primary_category="cafe",
        confidence=0.9,
        road_address=None,
        phone=None,
        website=None,
        sources=[],
    )
    return build_provider_cafe_catalog((overture,), (), (_kakao("123"),))


def _write_verified_kakao_cache(tmp_path, records=(_kakao("123"),)):
    path = tmp_path / "kakao.jsonl"
    cache_bytes = b"".join(
        (
            json.dumps(
                record.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for record in records
    )
    path.write_bytes(cache_bytes)
    manifest = path.with_suffix(path.suffix + ".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "complete": True,
                "category_group_code": KAKAO_CAFE_CATEGORY_CODE,
                "bbox": list(SEOUL_BBOX),
                "unresolved_count": 0,
                "record_count": len(records),
                "cache_sha256": hashlib.sha256(cache_bytes).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return path, manifest


def test_read_kakao_cache_validates_jsonl(tmp_path) -> None:
    path, manifest = _write_verified_kakao_cache(tmp_path)

    records = read_kakao_cache(path, manifest)

    assert len(records) == 1
    assert records[0].place_id == "123"

    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Kakao cache line 1"):
        read_kakao_cache(path, manifest)


def test_read_kakao_cache_requires_manifest(tmp_path) -> None:
    path, manifest = _write_verified_kakao_cache(tmp_path)
    manifest.unlink()

    with pytest.raises(ValueError, match="invalid or missing Kakao cache manifest"):
        read_kakao_cache(path, manifest)


def test_read_kakao_cache_rejects_incomplete_manifest(tmp_path) -> None:
    path, manifest = _write_verified_kakao_cache(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["complete"] = False
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest is incomplete"):
        read_kakao_cache(path, manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 999, "schema_version"),
        ("category_group_code", "FD6", "category_group_code"),
        ("bbox", [126.0, 37.0, 127.0, 38.0], "bbox"),
    ],
)
def test_read_kakao_cache_rejects_wrong_manifest_contract(
    tmp_path, field, value, message
) -> None:
    path, manifest = _write_verified_kakao_cache(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[field] = value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        read_kakao_cache(path, manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("record_count", 2, "record_count"),
        ("cache_sha256", "0" * 64, "sha256"),
    ],
)
def test_read_kakao_cache_rejects_manifest_count_or_hash_mismatch(
    tmp_path, field, value, message
) -> None:
    path, manifest = _write_verified_kakao_cache(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[field] = value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        read_kakao_cache(path, manifest)


def test_publish_provider_build_is_atomic_and_refuses_overwrite(tmp_path) -> None:
    output = tmp_path / "nested" / "provider.jsonl"
    manifest = tmp_path / "nested" / "provider.manifest.json"
    build = _build()

    publish_build(output, manifest, build)

    assert output.is_file()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["complete"] is True
    assert manifest_payload["record_count"] == 1
    assert not list(output.parent.glob("*.part"))
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        publish_build(output, manifest, build)


def test_publish_provider_build_rolls_back_first_link_when_manifest_fails(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "provider.jsonl"
    manifest = tmp_path / "provider.manifest.json"
    real_link = __import__("os").link
    calls = 0

    def fail_second_link(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("manifest publish failed")
        return real_link(source, destination)

    monkeypatch.setattr("scripts.build_provider_cafe_catalog.os.link", fail_second_link)

    with pytest.raises(OSError, match="manifest publish failed"):
        publish_build(output, manifest, _build())

    assert not output.exists()
    assert not manifest.exists()
