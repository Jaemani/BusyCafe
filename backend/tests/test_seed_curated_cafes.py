from __future__ import annotations

import pytest

from app.ingest.overture_places import OvertureCafeRecord, OvertureSeedReport
from scripts.seed_curated_cafes import CuratedSeedError, stage_curated_seed


def _record() -> OvertureCafeRecord:
    return OvertureCafeRecord(
        overture_id="overture:test",
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


def _report(*, dry_run: bool, deactivated: int = 0) -> OvertureSeedReport:
    return OvertureSeedReport(
        source_count=1,
        inserted_count=1,
        updated_count=0,
        unchanged_count=0,
        deactivated_count=deactivated,
        active_count=1,
        dry_run=dry_run,
    )


def test_default_stage_is_dry_run_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    def fake_seed(*args: object, dry_run: bool, **kwargs: object) -> OvertureSeedReport:
        calls.append(dry_run)
        return _report(dry_run=dry_run)

    monkeypatch.setattr("scripts.seed_curated_cafes.seed_overture_cafes", fake_seed)
    stage = stage_curated_seed(object(), [_record()], release="test", apply=False)  # type: ignore[arg-type]

    assert calls == [True]
    assert stage.applied is None


def test_apply_refuses_before_write_when_deactivation_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def fake_seed(*args: object, dry_run: bool, **kwargs: object) -> OvertureSeedReport:
        calls.append(dry_run)
        return _report(dry_run=dry_run, deactivated=1)

    monkeypatch.setattr("scripts.seed_curated_cafes.seed_overture_cafes", fake_seed)
    with pytest.raises(CuratedSeedError, match="would deactivate 1"):
        stage_curated_seed(object(), [_record()], release="test", apply=True)  # type: ignore[arg-type]

    assert calls == [True]


def test_explicit_safe_apply_runs_only_after_clean_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def fake_seed(*args: object, dry_run: bool, **kwargs: object) -> OvertureSeedReport:
        calls.append(dry_run)
        return _report(dry_run=dry_run)

    monkeypatch.setattr("scripts.seed_curated_cafes.seed_overture_cafes", fake_seed)
    stage = stage_curated_seed(object(), [_record()], release="test", apply=True)  # type: ignore[arg-type]

    assert calls == [True, False]
    assert stage.applied is not None
