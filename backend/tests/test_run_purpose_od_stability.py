from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.config import PURPOSE_OD_SEOUL_ZONE_COUNT, PURPOSE_OD_STABILITY_HOURS
from scripts import run_purpose_od_stability as stability


START = date(2026, 6, 9)


def _artifact(
    path: Path,
    *,
    observed_date: date,
    week: int = 0,
    purpose_shift: bool = False,
    direction_strength: float = 0.5,
    direction_offset_deg: float = 0.0,
    centroid_sha: str = "centroid-sha",
) -> Path:
    movements: list[dict] = []
    for hour in PURPOSE_OD_STABILITY_HOURS:
        for index in range(PURPOSE_OD_SEOUL_ZONE_COUNT):
            zone = f"{11_000_000 + index:08d}"
            net = float(index - PURPOSE_OD_SEOUL_ZONE_COUNT // 2)
            inbound = float(1_000 + index * 2 + week * 10 + hour)
            outbound = inbound - net
            if purpose_shift and week % 2:
                ratios = (0.70, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05)
            else:
                ratios = (0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.70)
            movements.append(
                {
                    "administrative_zone_code": zone,
                    "zone_name": f"동 {index}",
                    "zone_kind": "seoul_admin_dong",
                    "hour": hour,
                    "local_time": f"{hour:02d}:00",
                    "inbound_estimated_count": inbound,
                    "outbound_estimated_count": outbound,
                    "net_estimated_count": net,
                    "intrazonal_inbound_estimated_count": 0.0,
                    "intrazonal_outbound_estimated_count": 0.0,
                    "purpose_estimated_counts": {
                        str(purpose): inbound * ratio
                        for purpose, ratio in enumerate(ratios, start=1)
                    },
                    "purpose_ratios": {
                        str(purpose): ratio
                        for purpose, ratio in enumerate(ratios, start=1)
                    },
                    "mean_source_distance_m": 1000.0,
                    "mean_source_duration_min": 20.0,
                    "zone_centroid": {"lat": 37.5, "lng": 127.0},
                    "geometry_coverage": {
                        "rows": 1,
                        "total_rows": 1,
                        "estimated_count": inbound,
                        "estimated_count_ratio": 1.0,
                    },
                    "movement_vector": {
                        "east_component": 0.0,
                        "north_component": direction_strength,
                        "travel_heading_deg": float(
                            (index + direction_offset_deg) % 360
                        ),
                        "origin_bearing_deg": float(
                            (index + direction_offset_deg + 180) % 360
                        ),
                        "direction_strength": direction_strength,
                        "eligible_estimated_count_coverage": 1.0,
                    },
                }
            )
    payload = {
        "artifact": {
            "model_version": "v1-purpose-od-movement-shadow",
            "public_model_effect": "none; offline shadow only",
        },
        "target": {
            "date": observed_date.isoformat(),
            "timezone": "Asia/Seoul",
            "hours": list(PURPOSE_OD_STABILITY_HOURS),
        },
        "source": {
            "id": "seoul-purpose-od",
            "version": f"test-{observed_date:%Y%m%d}",
            "schema_version": "oa-22300-purpose-od-csv-v1",
            "file": path.with_suffix(".zip").name,
            "size_bytes": 1,
            "sha256": f"source-{observed_date:%Y%m%d}",
        },
        "centroids": {
            "available": True,
            "schema_version": "purpose-od-centroids-v1",
            "crs": "EPSG:4326",
            "file": "centroids.json",
            "sha256": centroid_sha,
            "source": {"version": "ver20260401"},
            "records": 682,
        },
        "coverage": {
            "source_rows_scanned": len(movements),
            "selected_rows": len(movements),
            "selected_estimated_count": 1.0,
            "observed_zone_codes": PURPOSE_OD_SEOUL_ZONE_COUNT,
            "matched_centroid_zone_codes": PURPOSE_OD_SEOUL_ZONE_COUNT,
            "centroid_code_ratio": 1.0,
            "complete_centroid_rows": len(movements),
            "complete_centroid_estimated_count": 1.0,
            "complete_centroid_row_ratio": 1.0,
            "complete_centroid_estimated_count_ratio": 1.0,
            "intrazonal_rows": 0,
            "intrazonal_estimated_count": 0.0,
            "missing_origin_codes": [],
            "missing_destination_codes": [],
        },
        "time_contract": {
            "normalization": "floor-to-hour; source mixed 60/20-minute bins",
            "distinct_source_start_bins": 36,
            "distinct_source_finish_bins": 36,
        },
        "provenance": {},
        "movements": movements,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return path


def _weekly(tmp_path: Path, **kwargs) -> list[Path]:
    return [
        _artifact(
            tmp_path / f"week-{week}.json",
            observed_date=START + timedelta(days=7 * week),
            week=week,
            **kwargs,
        )
        for week in range(4)
    ]


def _run(tmp_path: Path, *, weekly: list[Path] | None = None, apply=False):
    return stability.run_stability_pilot(
        weekly_paths=weekly or _weekly(tmp_path),
        descriptive_paths=[],
        output_path=tmp_path / "report.json",
        apply=apply,
    )


def test_supported_weekly_scalar_purpose_and_vector(tmp_path: Path) -> None:
    result = _run(tmp_path)
    weekly = result.report["weekly_summary"]

    assert weekly["scalar"]["verdict"] == "supported"
    assert weekly["scalar"]["net_spearman"]["minimum"] == 1.0
    assert weekly["scalar"]["top_decile_jaccard"]["median"] == 1.0
    assert weekly["purpose"]["verdict"] == "stable"
    assert weekly["purpose"]["js_distance"]["median"] == 0.0
    assert weekly["vector"]["verdict"] == "usable"
    assert result.report["decision"] == {
        "historical_scalar_prior_candidate": True,
        "purpose_feature_candidate": True,
        "vector_feature_candidate": True,
        "accuracy_claim_allowed": False,
        "public_promotion_allowed": False,
    }


def test_unstable_purpose_and_weak_vector_are_separate_from_scalar(
    tmp_path: Path,
) -> None:
    weekly = _weekly(
        tmp_path, purpose_shift=True, direction_strength=0.10
    )
    result = _run(tmp_path, weekly=weekly).report["weekly_summary"]

    assert result["scalar"]["verdict"] == "supported"
    assert result["purpose"]["verdict"] == "not_stable"
    assert result["vector"]["verdict"] == "not_usable"
    assert result["vector"]["eligible_ratio"]["minimum"] == 0.0


@pytest.mark.parametrize(
    (
        "direction_offset_deg",
        "direction_strength",
        "summary_key",
        "expected_max",
    ),
    [
        (40.0, 0.5, "angle_median_deg", 40.0),
        (0.0, 0.65, "strength_delta_median", 0.15),
    ],
)
def test_every_weekly_pair_must_pass_vector_thresholds(
    tmp_path: Path,
    direction_offset_deg: float,
    direction_strength: float,
    summary_key: str,
    expected_max: float,
) -> None:
    weekly = [
        _artifact(
            tmp_path / f"week-{week}.json",
            observed_date=START + timedelta(days=7 * week),
            week=week,
            direction_offset_deg=direction_offset_deg if week >= 2 else 0.0,
            direction_strength=direction_strength if week >= 2 else 0.5,
        )
        for week in range(4)
    ]

    vector = _run(tmp_path, weekly=weekly).report["weekly_summary"]["vector"]

    assert vector["verdict"] == "not_usable"
    assert vector[summary_key]["maximum"] == pytest.approx(expected_max)


def test_deterministic_dry_run_atomic_apply_and_overwrite_refusal(
    tmp_path: Path,
) -> None:
    weekly = _weekly(tmp_path)
    first = _run(tmp_path, weekly=weekly)
    second = stability.run_stability_pilot(
        weekly_paths=list(reversed(weekly)),
        descriptive_paths=[],
        output_path=tmp_path / "second.json",
    )
    assert first.serialized == second.serialized
    assert not first.output_path.exists()

    applied = _run(tmp_path, weekly=weekly, apply=True)
    assert applied.output_path.read_bytes() == applied.serialized
    assert not (tmp_path / "report.json.part").exists()
    with pytest.raises(stability.PurposeOdStabilityError, match="overwrite"):
        _run(tmp_path, weekly=weekly)


def test_rejects_non_adjacent_week_or_centroid_contract_mismatch(
    tmp_path: Path,
) -> None:
    weekly = _weekly(tmp_path)
    replacement = _artifact(
        tmp_path / "bad-date.json",
        observed_date=START + timedelta(days=28),
        week=3,
    )
    with pytest.raises(stability.PurposeOdStabilityError, match="seven-day"):
        _run(tmp_path, weekly=[*weekly[:3], replacement])

    mismatch = _artifact(
        tmp_path / "bad-centroid.json",
        observed_date=START + timedelta(days=21),
        week=3,
        centroid_sha="different",
    )
    with pytest.raises(stability.PurposeOdStabilityError, match="centroid"):
        _run(tmp_path, weekly=[*weekly[:3], mismatch])


def test_tie_aware_spearman_js_distance_and_angle_helpers() -> None:
    assert stability._spearman([1, 1, 2, 3], [1, 1, 2, 3]) == pytest.approx(1)
    assert stability._spearman([1, 1], [2, 2]) is None
    assert stability._js_distance((0.5, 0.5), (0.5, 0.5)) == 0
    assert stability._js_distance((1.0, 0.0), (0.0, 1.0)) == pytest.approx(1)
    assert stability._angle_difference(350, 10) == 20
