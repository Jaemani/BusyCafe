from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.clients.seoul_refreshment_permits import parse_permit_page
from app.ingest.seoul_refreshment_candidates import resolve_permit_candidates
from scripts.cache_refreshment_candidates import (
    CandidateCacheError,
    build_manifest,
    fetch_candidate_resolution,
    main,
    publish_cache,
    read_candidate_cache,
    serialize_candidates,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "seoul_refreshment_permits_sample.json"
)


def _fixture_page():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["LOCALDATA_072405"]["list_total_count"] = 3
    return parse_permit_page(payload)


def test_fetch_resolution_checks_moving_total_and_page_size() -> None:
    page = _fixture_page()
    first = page.model_copy(update={"total_count": 3, "rows": page.rows[:2]})
    second = page.model_copy(update={"total_count": 4, "rows": page.rows[2:]})

    with pytest.raises(CandidateCacheError, match="source total changed"):
        fetch_candidate_resolution(
            lambda start, end: first if start == 1 else second,
            page_size=2,
        )


def test_publish_is_deterministic_atomic_and_refuses_overwrite(tmp_path: Path) -> None:
    resolution = resolve_permit_candidates(_fixture_page().rows)
    cache = tmp_path / "candidates.jsonl"
    manifest = tmp_path / "manifest.json"

    publish_cache(cache, manifest, resolution)

    cache_bytes = cache.read_bytes()
    assert read_candidate_cache(cache) == resolution.candidates
    aggregate = json.loads(manifest.read_text(encoding="utf-8"))
    assert aggregate == build_manifest(resolution, cache_bytes)
    assert "스타벅스" not in manifest.read_text(encoding="utf-8")
    assert "강남대로" not in manifest.read_text(encoding="utf-8")
    assert serialize_candidates(resolution.candidates) == cache_bytes
    assert aggregate["facility_area_eligible_count"] == 1
    assert aggregate["facility_area_missing_count"] == 0
    assert aggregate["facility_area_nonpositive_count"] == 0
    serialized = json.loads(cache_bytes.decode("utf-8"))
    assert serialized["facility_area_m2"] == "125.5"
    assert isinstance(serialized["facility_area_m2"], str)
    assert not list(tmp_path.glob("tmp*"))

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        publish_cache(cache, manifest, resolution)


def test_old_cache_without_area_evidence_remains_readable(tmp_path: Path) -> None:
    cache = tmp_path / "old-candidates.jsonl"
    cache.write_text(
        json.dumps(
            {
                "source": "legacy",
                "source_id": "old-1",
                "name": "legacy cafe",
                "latitude": 37.5,
                "longitude": 127.0,
                "category": "커피숍",
                "road_address": None,
                "lot_address": None,
                "phone": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    candidate = read_candidate_cache(cache)[0]

    assert candidate.facility_area_raw is None
    assert candidate.facility_area_m2 is None
    assert candidate.facility_area_status is None


def test_manifest_counts_missing_nonnumeric_and_nonpositive_area() -> None:
    page = _fixture_page()
    row_model = type(page.rows[0])
    base = page.rows[0].model_dump(mode="json", by_alias=True)
    rows = []
    for index, raw in enumerate(("10", "", "not-a-number", "0", "-1")):
        payload = dict(base)
        payload.update(
            {
                "MGTNO": f"area-{index}",
                "BPLCNM": f"area cafe {index}",
                "FACILTOTSCP": raw,
                # Positive SITEAREA must never rescue an invalid FACILTOTSCP.
                "SITEAREA": "999",
            }
        )
        rows.append(row_model.model_validate(payload))
    resolution = resolve_permit_candidates(rows)

    manifest = build_manifest(resolution, serialize_candidates(resolution.candidates))

    assert manifest["facility_area_eligible_count"] == 1
    assert manifest["facility_area_missing_count"] == 2
    assert manifest["facility_area_nonpositive_count"] == 2


def test_review_sample_mode_reads_cache_without_network_or_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resolution = resolve_permit_candidates(_fixture_page().rows)
    cache = tmp_path / "candidates.jsonl"
    cache.write_bytes(serialize_candidates(resolution.candidates))

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("review sample must not access network")

    monkeypatch.setattr(
        "scripts.cache_refreshment_candidates.SeoulRefreshmentPermitClient", fail
    )
    assert main(["--output", str(cache), "--review-sample", "1"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["source_id"] == resolution.candidates[0].source_id


def test_default_mode_refuses_existing_cache_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "candidates.jsonl"
    cache.write_text("existing", encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("overwrite refusal must happen before network")

    monkeypatch.setattr(
        "scripts.cache_refreshment_candidates.SeoulRefreshmentPermitClient", fail
    )
    assert main(["--output", str(cache)]) == 1
