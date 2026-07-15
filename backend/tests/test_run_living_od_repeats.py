from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import (
    LIVING_OD_REPEATS_DESCRIPTIVE_DATES,
    LIVING_OD_REPEATS_DISCOVERY_DATE,
    LIVING_OD_REPEATS_HELD_OUT_DATES,
    LIVING_OD_SAME_DAY_HOURS,
    PURPOSE_OD_SEOUL_ZONE_COUNT,
)
from scripts import run_living_od_repeats as repeats
from scripts import run_living_od_same_day as same_day


HEADER = "일자,시간,행정동코드,250M격자,생활인구합계"


def _zone(index: int) -> str:
    return f"{11_000_000 + index:08d}"


def _cell(index: int) -> str:
    east = 5_000 + (index % 200) * 25
    north = 5_000 + (index // 200) * 25
    return f"다사{east:04d}{north:04d}"


def _artifact(path: Path, observed_date: str) -> Path:
    movements: list[dict] = []
    for hour in LIVING_OD_SAME_DAY_HOURS:
        for index in range(PURPOSE_OD_SEOUL_ZONE_COUNT):
            net = float(index - PURPOSE_OD_SEOUL_ZONE_COUNT // 2)
            gross = 2_000.0 + index * 2.0
            movements.append(
                {
                    "administrative_zone_code": _zone(index),
                    "zone_kind": "seoul_admin_dong",
                    "hour": hour,
                    "inbound_estimated_count": (gross + net) / 2.0,
                    "outbound_estimated_count": (gross - net) / 2.0,
                    "net_estimated_count": net,
                }
            )
    payload = {
        "artifact": {
            "model_version": "v1-purpose-od-movement-shadow",
            "public_model_effect": "none; offline shadow only",
        },
        "target": {
            "date": observed_date,
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


def _living_csv(path: Path, observed_date: str) -> Path:
    compact_date = observed_date.replace("-", "")
    rows: list[str] = []
    for target_hour in LIVING_OD_SAME_DAY_HOURS:
        for hour in (target_hour - 1, target_hour, target_hour + 1):
            for index in range(PURPOSE_OD_SEOUL_ZONE_COUNT):
                net = float(index - PURPOSE_OD_SEOUL_ZONE_COUNT // 2)
                stock = 10_000.0 + index * 10.0
                total = stock + (hour - target_hour) * net
                rows.append(
                    f"{compact_date},{hour:02d},{_zone(index)},"
                    f"{_cell(index)},{total:.6f}"
                )
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="cp949")
    return path


def _all_pairs(tmp_path: Path) -> list[repeats.RepeatPairInput]:
    roles: dict[str, repeats.PairRole] = {
        **{value: "held_out" for value in LIVING_OD_REPEATS_HELD_OUT_DATES},
        LIVING_OD_REPEATS_DISCOVERY_DATE: "discovery",
        **{
            value: "descriptive_only"
            for value in LIVING_OD_REPEATS_DESCRIPTIVE_DATES
        },
    }
    pairs: list[repeats.RepeatPairInput] = []
    for observed_date, role in roles.items():
        token = observed_date.replace("-", "")
        pairs.append(
            repeats.RepeatPairInput(
                role=role,
                observed_date=repeats._parse_date(observed_date),
                living_population_path=_living_csv(
                    tmp_path / f"living-{token}.csv", observed_date
                ),
                purpose_od_path=_artifact(
                    tmp_path / f"od-{token}.json", observed_date
                ),
            )
        )
    return pairs


def test_reuses_full_v2_reports_and_supported_gate(tmp_path: Path) -> None:
    pairs = _all_pairs(tmp_path)

    result = repeats.run_living_od_repeats(
        pairs=list(reversed(pairs)), output_path=tmp_path / "report.json"
    )

    assert result.report["scope"]["public_model_effect"] == (
        "none; offline shadow only"
    )
    assert result.report["decision"] == {
        "base_verdict": "supported",
        "verdict": "supported",
        "imputation_sensitive": False,
        "historical_feature_candidate": False,
        "accuracy_claim_allowed": False,
        "public_promotion_allowed": False,
    }
    held_out = result.report["held_out_summary"]
    assert held_out["primary_rho_count"] == 9
    assert held_out["pooled_median"] == 1.0
    assert held_out["pooled_minimum"] == 1.0
    assert len(result.report["pairs"]) == 6
    assert all(
        item["pair_report"]["report_version"].startswith("v2-")
        and len(item["pair_report_sha256"]) == 64
        for item in result.report["pairs"]
    )
    assert not result.output_path.exists()


def test_deterministic_atomic_apply_and_overwrite_refusal(tmp_path: Path) -> None:
    pairs = _all_pairs(tmp_path)
    first = repeats.run_living_od_repeats(
        pairs=pairs, output_path=tmp_path / "report.json"
    )
    second = repeats.run_living_od_repeats(
        pairs=list(reversed(pairs)), output_path=tmp_path / "second.json"
    )
    assert first.serialized == second.serialized

    applied = repeats.run_living_od_repeats(
        pairs=pairs, output_path=tmp_path / "report.json", apply=True
    )
    assert applied.output_path.read_bytes() == applied.serialized
    assert not (tmp_path / "report.json.part").exists()
    with pytest.raises(repeats.LivingOdRepeatsError, match="overwrite"):
        repeats.run_living_od_repeats(
            pairs=pairs, output_path=tmp_path / "report.json"
        )


def test_exact_role_and_date_set_fail_closed(tmp_path: Path) -> None:
    pairs = _all_pairs(tmp_path)
    with pytest.raises(repeats.LivingOdRepeatsError, match="exactly 6"):
        repeats.run_living_od_repeats(
            pairs=pairs[:-1], output_path=tmp_path / "report.json"
        )

    wrong_role = [*pairs]
    index = next(i for i, item in enumerate(wrong_role) if item.role == "discovery")
    wrong_role[index] = replace(wrong_role[index], role="held_out")
    with pytest.raises(repeats.LivingOdRepeatsError, match="role must be discovery"):
        repeats.run_living_od_repeats(
            pairs=wrong_role, output_path=tmp_path / "report.json"
        )


def _stub_report(
    observed_date: str,
    *,
    verdict: str,
    rhos: tuple[float, float, float],
    sensitive: bool,
) -> same_day.SameDayResult:
    report = {
        "report_version": "v2-fixture",
        "scope": {
            "date": observed_date,
            "public_model_effect": "none; offline shadow only",
        },
        "imputation_sensitivity": {"primary_variant": "primary"},
        "correlations_by_variant": {
            "primary": {
                "primary_net_vs_next_stock_delta": [
                    {"hour": hour, "spearman_rho": rho}
                    for hour, rho in zip(
                        LIVING_OD_SAME_DAY_HOURS, rhos, strict=True
                    )
                ]
            }
        },
        "decision": {
            "base_verdict": verdict,
            "verdict": verdict,
            "imputation_sensitive": sensitive,
        },
    }
    serialized = json.dumps(report, sort_keys=True).encode()
    return same_day.SameDayResult(report, serialized, Path("unused.json"))


def _mock_pairs() -> list[repeats.RepeatPairInput]:
    expected = repeats._expected_roles()
    return [
        repeats.RepeatPairInput(role, observed_date, Path("living"), Path("od"))
        for observed_date, role in expected.items()
    ]


def test_conditional_gate_and_held_out_sensitivity_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held_out = list(LIVING_OD_REPEATS_HELD_OUT_DATES)
    outcomes = {
        held_out[0]: ("screening", (0.4, 0.4, 0.4), False),
        held_out[1]: ("conditional", (0.3, 0.3, 0.3), False),
        held_out[2]: ("not_supported", (0.3, -0.1, -0.1), False),
    }

    def fake_same_day(**kwargs):
        token = kwargs["target_date"].isoformat()
        verdict, rhos, sensitive = outcomes.get(
            token, ("screening", (1.0, 1.0, 1.0), False)
        )
        return _stub_report(
            token, verdict=verdict, rhos=rhos, sensitive=sensitive
        )

    monkeypatch.setattr(repeats.same_day, "run_living_od_same_day", fake_same_day)
    conditional = repeats.run_living_od_repeats(
        pairs=_mock_pairs(), output_path=tmp_path / "conditional.json"
    )
    assert conditional.report["decision"]["base_verdict"] == "conditional"
    assert conditional.report["decision"]["verdict"] == "conditional"
    assert conditional.report["held_out_summary"]["positive_rho_count"] == 7

    outcomes[held_out[1]] = ("screening", (0.4, 0.4, 0.4), False)
    outcomes[held_out[2]] = ("screening", (0.4, 0.4, 0.4), True)
    degraded = repeats.run_living_od_repeats(
        pairs=_mock_pairs(), output_path=tmp_path / "degraded.json"
    )
    assert degraded.report["decision"]["base_verdict"] == "supported"
    assert degraded.report["decision"]["verdict"] == "conditional"
    assert degraded.report["decision"]["imputation_sensitive"] is True


def test_pair_gate_failure_invalidates_whole_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_pair(**kwargs):
        raise same_day.LivingOdSameDayError("bare-cell Jaccard below gate")

    monkeypatch.setattr(repeats.same_day, "run_living_od_same_day", fail_pair)
    with pytest.raises(same_day.LivingOdSameDayError, match="Jaccard"):
        repeats.run_living_od_repeats(
            pairs=_mock_pairs(), output_path=tmp_path / "report.json"
        )
