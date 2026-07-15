from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import (
    LIVING_OD_SAME_DAY_HOURS,
    PURPOSE_OD_SEOUL_ZONE_COUNT,
)
from scripts import run_living_od_same_day as same_day


HEADER = "일자,시간,행정동코드,250M격자,생활인구합계"


def _zone(index: int) -> str:
    return f"{11_000_000 + index:08d}"


def _cell(index: int, variant: int = 0) -> str:
    grid_index = index * 2 + variant
    east = 5_000 + (grid_index % 200) * 25
    north = 5_000 + (grid_index // 200) * 25
    return f"다사{east:04d}{north:04d}"


def _net(index: int, hour: int, *, sensitive_hour: int | None) -> float:
    if hour == sensitive_hour:
        return index / 1_000.0
    return float(index - PURPOSE_OD_SEOUL_ZONE_COUNT // 2)


def _artifact(path: Path, *, sensitive_hour: int | None = None) -> Path:
    movements: list[dict] = []
    for hour in LIVING_OD_SAME_DAY_HOURS:
        for index in range(PURPOSE_OD_SEOUL_ZONE_COUNT):
            net = _net(index, hour, sensitive_hour=sensitive_hour)
            gross = 2_000.0 + index * 2.0
            inbound = (gross + net) / 2.0
            outbound = (gross - net) / 2.0
            movements.append(
                {
                    "administrative_zone_code": _zone(index),
                    "zone_kind": "seoul_admin_dong",
                    "hour": hour,
                    "inbound_estimated_count": inbound,
                    "outbound_estimated_count": outbound,
                    "net_estimated_count": net,
                }
            )
    payload = {
        "artifact": {
            "model_version": "v1-purpose-od-movement-shadow",
            "public_model_effect": "none; offline shadow only",
        },
        "target": {
            "date": "2026-06-30",
            "timezone": "Asia/Seoul",
            "hours": list(LIVING_OD_SAME_DAY_HOURS),
        },
        "source": {
            "id": "seoul-purpose-od",
            "schema_version": "fixture-purpose-od-v1",
            "sha256": "a" * 64,
        },
        "movements": movements,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return path


def _living_csv(
    path: Path,
    *,
    omit: set[tuple[int, int]] | None = None,
    duplicate: bool = False,
    wrong_date: bool = False,
    sensitive_hour: int | None = None,
    shared_cell: bool = False,
) -> Path:
    omitted = omit or set()
    rows: list[str] = []
    for hour in sorted(
        adjacent
        for target in LIVING_OD_SAME_DAY_HOURS
        for adjacent in (target - 1, target, target + 1)
    ):
        target_hour = next(
            target
            for target in LIVING_OD_SAME_DAY_HOURS
            if hour in (target - 1, target, target + 1)
        )
        for index in range(PURPOSE_OD_SEOUL_ZONE_COUNT):
            if (hour, index) in omitted:
                continue
            net = _net(index, target_hour, sensitive_hour=sensitive_hour)
            stock = 10_000.0 + index * 10.0
            if target_hour == sensitive_hour:
                mask_count = 1 + index % 2
                stock = mask_count * 2.0 - net
            if hour == target_hour - 1:
                total: str = f"{stock - net:.6f}"
            elif hour == target_hour:
                total = f"{stock:.6f}"
            elif target_hour == sensitive_hour:
                total = "*"
            else:
                total = f"{stock + net:.6f}"
            observed_date = "20260629" if wrong_date and not rows else "20260630"
            cell_id = _cell(0) if shared_cell and index == 1 else _cell(index)
            rows.append(
                f"{observed_date},{hour:02d},{_zone(index)},{cell_id},{total}"
            )
            if target_hour == sensitive_hour and index % 2:
                variant_total = "*" if hour == target_hour + 1 else "0"
                rows.append(
                    f"20260630,{hour:02d},{_zone(index)},{_cell(index, 1)},"
                    f"{variant_total}"
                )
    if duplicate:
        rows.append(rows[0])
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="cp949")
    return path


def _inputs(tmp_path: Path, *, sensitive_hour: int | None = None) -> tuple[Path, Path]:
    return (
        _living_csv(
            tmp_path / "living.csv",
            sensitive_hour=sensitive_hour,
        ),
        _artifact(tmp_path / "purpose.json", sensitive_hour=sensitive_hour),
    )


def _run(
    tmp_path: Path,
    *,
    living: Path | None = None,
    purpose: Path | None = None,
    output_name: str = "report.json",
    apply: bool = False,
):
    if living is None or purpose is None:
        default_living, default_purpose = _inputs(tmp_path)
        living = living or default_living
        purpose = purpose or default_purpose
    return same_day.run_living_od_same_day(
        living_population_path=living,
        purpose_od_path=purpose,
        output_path=tmp_path / output_name,
        apply=apply,
    )


def test_screening_metrics_masking_and_fixed_alignment(tmp_path: Path) -> None:
    living, purpose = _inputs(tmp_path)
    result = _run(tmp_path, living=living, purpose=purpose).report

    assert result["scope"]["public_model_effect"] == "none; offline shadow only"
    assert result["scope"]["iid_p_values"] is None
    assert [item["code_coverage"] for item in result["coverage"]] == [1.0] * 3
    assert [
        item["minimum_jaccard"]
        for item in result["zone_cell_universe_stability"]
    ] == [1.0] * 3
    primary = result["correlations_by_imputation"]["2"]
    assert primary["verdict"] == "screening"
    assert [
        item["spearman_rho"]
        for item in primary["primary_net_vs_next_stock_delta"]
    ] == [1.0, 1.0, 1.0]
    assert [
        item["spearman_rho"]
        for item in primary["secondary_net_vs_previous_stock_delta"]
    ] == [1.0, 1.0, 1.0]
    assert [
        item["spearman_rho"]
        for item in primary["secondary_gross_flow_vs_stock"]
    ] == [1.0, 1.0, 1.0]
    assert result["decision"] == {
        "base_verdict": "screening",
        "verdict": "screening",
        "imputation_sensitive": False,
        "historical_feature_candidate": False,
        "accuracy_claim_allowed": False,
        "public_promotion_allowed": False,
    }
    assert result["inputs"]["living_population"]["source_rows"] == (
        PURPOSE_OD_SEOUL_ZONE_COUNT * 9
    )


def test_imputation_sensitivity_is_separate_and_degrades_verdict(tmp_path: Path) -> None:
    living, purpose = _inputs(tmp_path, sensitive_hour=8)
    result = _run(tmp_path, living=living, purpose=purpose).report

    sensitivity = result["imputation_sensitivity"]
    assert sensitivity["imputation_sensitive"] is True
    assert any(item["range_exceeded"] for item in sensitivity["hours"])
    assert result["decision"]["base_verdict"] == "screening"
    assert result["decision"]["verdict"] == "conditional"
    hour_9 = next(
        item
        for item in result["living_population_by_imputation"]["2"]
        if item["hour"] == 9
    )
    assert hour_9["masked_rows"] > PURPOSE_OD_SEOUL_ZONE_COUNT
    assert hour_9["masked_row_ratio"] > 0


def test_coverage_below_fixed_threshold_fails_closed(tmp_path: Path) -> None:
    missing = {(9, index) for index in range(22)}
    living = _living_csv(tmp_path / "living.csv", omit=missing)
    purpose = _artifact(tmp_path / "purpose.json")

    with pytest.raises(same_day.LivingOdSameDayError, match="code coverage"):
        _run(tmp_path, living=living, purpose=purpose)


def test_same_cell_may_have_distinct_administrative_zone_rows(
    tmp_path: Path,
) -> None:
    living = _living_csv(tmp_path / "living.csv", shared_cell=True)
    purpose = _artifact(tmp_path / "purpose.json")

    result = _run(tmp_path, living=living, purpose=purpose).report

    source = result["inputs"]["living_population"]
    assert source["unique_cells"] == PURPOSE_OD_SEOUL_ZONE_COUNT - 1
    assert source["unique_zone_cell_pairs"] == PURPOSE_OD_SEOUL_ZONE_COUNT


def test_od_hours_must_share_exact_zone_universe(tmp_path: Path) -> None:
    living = _living_csv(tmp_path / "living.csv")
    purpose = _artifact(tmp_path / "purpose.json")
    payload = json.loads(purpose.read_text(encoding="utf-8"))
    first_hour_14 = next(
        item for item in payload["movements"] if item["hour"] == 14
    )
    first_hour_14["administrative_zone_code"] = "99999999"
    purpose.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(same_day.LivingOdSameDayError, match="zone universe"):
        _run(tmp_path, living=living, purpose=purpose)


@pytest.mark.parametrize(
    ("duplicate", "wrong_date", "message"),
    [
        (True, False, "duplicate living-population date/hour/zone/cell"),
        (False, True, "outside fixed date"),
    ],
)
def test_full_csv_duplicate_and_date_validation(
    tmp_path: Path,
    duplicate: bool,
    wrong_date: bool,
    message: str,
) -> None:
    living = _living_csv(
        tmp_path / "living.csv", duplicate=duplicate, wrong_date=wrong_date
    )
    purpose = _artifact(tmp_path / "purpose.json")

    with pytest.raises(same_day.LivingOdSameDayError, match=message):
        _run(tmp_path, living=living, purpose=purpose)


def test_deterministic_dry_run_atomic_apply_and_overwrite_refusal(
    tmp_path: Path,
) -> None:
    living, purpose = _inputs(tmp_path)
    first = _run(tmp_path, living=living, purpose=purpose)
    second = _run(
        tmp_path,
        living=living,
        purpose=purpose,
        output_name="second.json",
    )
    assert first.serialized == second.serialized
    assert not first.output_path.exists()

    applied = _run(tmp_path, living=living, purpose=purpose, apply=True)
    assert applied.output_path.read_bytes() == applied.serialized
    assert not (tmp_path / "report.json.part").exists()
    with pytest.raises(same_day.LivingOdSameDayError, match="overwrite"):
        _run(tmp_path, living=living, purpose=purpose)


def test_average_tie_spearman_and_verdict_thresholds() -> None:
    assert same_day._spearman([1, 1, 2, 3], [1, 1, 2, 3]) == pytest.approx(1)
    assert same_day._spearman([1, 1, 2, 3], [3, 3, 2, 1]) == pytest.approx(-1)
    assert same_day._spearman([1, 1], [2, 2]) is None
    assert same_day._verdict([0.3, 0.4, 0.5]) == "screening"
    assert same_day._verdict([0.21, 0.25, -0.1]) == "conditional"
    assert same_day._verdict([0.1, 0.1, 0.1]) == "not_supported"
