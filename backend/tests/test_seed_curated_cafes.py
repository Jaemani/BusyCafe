from __future__ import annotations

import pytest

from app.ingest.overture_places import (
    NumericDeltaSummary,
    OvertureCafeRecord,
    OvertureSeedReport,
)
from scripts.seed_curated_cafes import (
    CuratedSeedError,
    _format_changed_field_counts,
    _format_delta_summary,
    stage_curated_seed,
)


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


def _report(
    *,
    dry_run: bool,
    deactivated: int = 0,
    changed_field_counts: tuple[tuple[str, int], ...] = (),
) -> OvertureSeedReport:
    return OvertureSeedReport(
        source_count=1,
        inserted_count=1,
        updated_count=0,
        unchanged_count=0,
        deactivated_count=deactivated,
        active_count=1,
        dry_run=dry_run,
        changed_field_counts=changed_field_counts,
    )


def test_changed_field_output_is_aggregate_only() -> None:
    report = _report(
        dry_run=True,
        changed_field_counts=(("phone", 3), ("source_release", 10)),
    )

    assert _format_changed_field_counts(report) == (
        "updated fields: phone=3, source_release=10"
    )
    assert _format_changed_field_counts(_report(dry_run=True)) == "updated fields: none"


def test_numeric_delta_output_is_aggregate_only() -> None:
    summary = NumericDeltaSummary(
        count=3,
        minimum=0.000001,
        p50=0.25,
        p95=1.5,
        maximum=2.0,
    )

    assert _format_delta_summary(
        "coordinate delta m", summary, include_minimum=True
    ) == "coordinate delta m: count=3, min=0.000001, p50=0.250000, p95=1.500000, max=2.000000"
    assert _format_delta_summary(
        "confidence absolute delta", summary, include_minimum=False
    ) == "confidence absolute delta: count=3, p50=0.250000, p95=1.500000, max=2.000000"
    assert (
        _format_delta_summary("coordinate delta m", None, include_minimum=True)
        == "coordinate delta m: none"
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
