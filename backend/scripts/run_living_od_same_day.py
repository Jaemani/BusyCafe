#!/usr/bin/env python3
"""Run the pre-registered same-day living-population/purpose-OD screen.

This offline evaluator compares OA-22300 zone/hour movement with OA-22784
zone/hour population stock on the fixed 2026-06-30 cross-section.  It is a
relationship screen, not an accuracy, causal, or independent-ground-truth
test.  No network or database is used.  Dry-run is the default; ``--apply``
atomically publishes one deterministic JSON report without overwriting files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Literal


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (  # noqa: E402
    LIVING_OD_SAME_DAY_CELL_UNIVERSE_JACCARD_MIN,
    LIVING_OD_SAME_DAY_CODE_COVERAGE_MIN,
    LIVING_OD_SAME_DAY_CONDITIONAL_MEDIAN_MIN,
    LIVING_OD_SAME_DAY_CONDITIONAL_POSITIVE_MIN,
    LIVING_OD_SAME_DAY_DATE,
    LIVING_OD_SAME_DAY_HOURS,
    LIVING_OD_SAME_DAY_IMPUTATION_RHO_RANGE_MAX,
    LIVING_OD_SAME_DAY_IMPUTATIONS,
    LIVING_OD_SAME_DAY_PRIMARY_IMPUTATION,
    LIVING_OD_SAME_DAY_REPORT_VERSION,
    LIVING_OD_SAME_DAY_SCREENING_MEDIAN_MIN,
    LIVING_POPULATION_HASH_CHUNK_BYTES,
    PURPOSE_OD_SEOUL_ZONE_COUNT,
    PURPOSE_OD_SHADOW_MODEL_VERSION,
)
from app.ingest.living_population import (  # noqa: E402
    LivingPopulationCsvError,
    iter_living_population_csv,
)


PART_SUFFIX = ".part"
SEOUL_ZONE_KIND = "seoul_admin_dong"
Verdict = Literal["screening", "conditional", "not_supported"]


class LivingOdSameDayError(ValueError):
    """Raised when inputs cannot support the pre-registered comparison."""


@dataclass(frozen=True, slots=True)
class _Movement:
    zone_code: str
    hour: int
    inbound: float
    outbound: float
    net: float


@dataclass(frozen=True, slots=True)
class _OdArtifact:
    path: Path
    sha256: str
    source_sha256: str
    source_schema_version: str
    movements: dict[tuple[str, int], _Movement]


@dataclass(slots=True)
class _LivingBucket:
    known_total: Decimal = Decimal(0)
    rows: int = 0
    masked_rows: int = 0

    def value(self, imputation: float) -> float:
        return float(
            self.known_total
            + Decimal(str(imputation)) * Decimal(self.masked_rows)
        )


@dataclass(frozen=True, slots=True)
class SameDayResult:
    report: dict[str, Any]
    serialized: bytes
    output_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(LIVING_POPULATION_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _dict(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LivingOdSameDayError(f"{field} must be an object")
    return value


def _list(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise LivingOdSameDayError(f"{field} must be an array")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LivingOdSameDayError(f"{field} must be canonical non-empty text")
    return value


def _sha256_text(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise LivingOdSameDayError(f"{field} must be a lowercase SHA-256")
    return text


def _number(value: object, *, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LivingOdSameDayError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise LivingOdSameDayError(f"{field} has invalid numeric value")
    return result


def _imputation_key(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _load_od_artifact(path: Path) -> _OdArtifact:
    resolved = path.resolve()
    if not resolved.is_file():
        raise LivingOdSameDayError(f"purpose OD artifact does not exist: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LivingOdSameDayError(
            f"cannot read purpose OD artifact {resolved}: {exc}"
        ) from exc
    root = _dict(payload, field="artifact root")
    artifact = _dict(root.get("artifact"), field="artifact")
    if artifact.get("model_version") != PURPOSE_OD_SHADOW_MODEL_VERSION:
        raise LivingOdSameDayError("purpose OD model_version mismatch")
    if artifact.get("public_model_effect") != "none; offline shadow only":
        raise LivingOdSameDayError("purpose OD artifact must remain shadow-only")

    target = _dict(root.get("target"), field="target")
    if target.get("date") != LIVING_OD_SAME_DAY_DATE:
        raise LivingOdSameDayError(
            f"purpose OD target.date must equal {LIVING_OD_SAME_DAY_DATE}"
        )
    if target.get("timezone") != "Asia/Seoul":
        raise LivingOdSameDayError("purpose OD timezone must equal Asia/Seoul")
    if _list(target.get("hours"), field="target.hours") != list(
        LIVING_OD_SAME_DAY_HOURS
    ):
        raise LivingOdSameDayError(
            f"purpose OD target.hours must equal {list(LIVING_OD_SAME_DAY_HOURS)}"
        )

    source = _dict(root.get("source"), field="source")
    if source.get("id") != "seoul-purpose-od":
        raise LivingOdSameDayError("purpose OD source.id mismatch")
    source_sha256 = _sha256_text(source.get("sha256"), field="source.sha256")
    source_schema_version = _text(
        source.get("schema_version"), field="source.schema_version"
    )

    movements: dict[tuple[str, int], _Movement] = {}
    for index, raw in enumerate(_list(root.get("movements"), field="movements")):
        item = _dict(raw, field=f"movements[{index}]")
        if item.get("zone_kind") != SEOUL_ZONE_KIND:
            continue
        zone_code = _text(
            item.get("administrative_zone_code"),
            field=f"movements[{index}].administrative_zone_code",
        )
        if len(zone_code) != 8 or not zone_code.isascii() or not zone_code.isdigit():
            raise LivingOdSameDayError("movement zone code must be eight ASCII digits")
        raw_hour = item.get("hour")
        if (
            isinstance(raw_hour, bool)
            or not isinstance(raw_hour, int)
            or raw_hour not in LIVING_OD_SAME_DAY_HOURS
        ):
            raise LivingOdSameDayError("movement hour outside fixed comparison hours")
        key = (zone_code, raw_hour)
        if key in movements:
            raise LivingOdSameDayError(f"duplicate purpose OD movement key: {key}")
        inbound = _number(
            item.get("inbound_estimated_count"),
            field=f"movements[{index}].inbound_estimated_count",
            minimum=0.0,
        )
        outbound = _number(
            item.get("outbound_estimated_count"),
            field=f"movements[{index}].outbound_estimated_count",
            minimum=0.0,
        )
        net = _number(
            item.get("net_estimated_count"),
            field=f"movements[{index}].net_estimated_count",
        )
        if not math.isclose(net, inbound - outbound, abs_tol=1e-6):
            raise LivingOdSameDayError("purpose OD net must equal inbound-outbound")
        movements[key] = _Movement(zone_code, raw_hour, inbound, outbound, net)

    zone_universes: list[set[str]] = []
    for hour in LIVING_OD_SAME_DAY_HOURS:
        zones = {
            zone for zone, candidate_hour in movements if candidate_hour == hour
        }
        if len(zones) != PURPOSE_OD_SEOUL_ZONE_COUNT:
            raise LivingOdSameDayError(
                f"purpose OD hour {hour} must contain "
                f"{PURPOSE_OD_SEOUL_ZONE_COUNT} Seoul zones; got {len(zones)}"
            )
        zone_universes.append(zones)
    if any(zones != zone_universes[0] for zones in zone_universes[1:]):
        raise LivingOdSameDayError(
            "purpose OD hours must share one exact Seoul zone universe"
        )
    return _OdArtifact(
        path=resolved,
        sha256=_sha256(resolved),
        source_sha256=source_sha256,
        source_schema_version=source_schema_version,
        movements=movements,
    )


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average = (start + 1 + end) / 2.0
        for index in range(start, end):
            ranks[ordered[index][0]] = average
        start = end
    return ranks


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = math.fsum(left_ranks) / len(left_ranks)
    right_mean = math.fsum(right_ranks) / len(right_ranks)
    numerator = math.fsum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_ranks, right_ranks, strict=True)
    )
    left_norm = math.sqrt(
        math.fsum((value - left_mean) ** 2 for value in left_ranks)
    )
    right_norm = math.sqrt(
        math.fsum((value - right_mean) ** 2 for value in right_ranks)
    )
    if left_norm == 0 or right_norm == 0:
        return None
    return numerator / (left_norm * right_norm)


def _verdict(rhos: Sequence[float | None]) -> Verdict:
    if len(rhos) != len(LIVING_OD_SAME_DAY_HOURS) or any(
        value is None for value in rhos
    ):
        return "not_supported"
    values = [value for value in rhos if value is not None]
    if (
        all(value > 0 for value in values)
        and median(values) >= LIVING_OD_SAME_DAY_SCREENING_MEDIAN_MIN
    ):
        return "screening"
    if (
        median(values) >= LIVING_OD_SAME_DAY_CONDITIONAL_MEDIAN_MIN
        and sum(value > 0 for value in values)
        >= LIVING_OD_SAME_DAY_CONDITIONAL_POSITIVE_MIN
    ):
        return "conditional"
    return "not_supported"


def _degrade(verdict: Verdict) -> Verdict:
    if verdict == "screening":
        return "conditional"
    return "not_supported"


def _round_optional(value: float | None) -> float | None:
    return None if value is None else round(float(value), 9)


def _preflight(
    living_population_path: Path,
    purpose_od_path: Path,
    output_path: Path,
) -> tuple[Path, Path, Path, Path]:
    living = living_population_path.resolve()
    purpose = purpose_od_path.resolve()
    output = output_path.resolve()
    part = output.with_name(output.name + PART_SUFFIX)
    for path, label in ((living, "living population CSV"), (purpose, "purpose OD")):
        if not path.is_file():
            raise LivingOdSameDayError(f"{label} input does not exist: {path}")
    if living == purpose:
        raise LivingOdSameDayError("input paths must be distinct")
    if output.suffix.lower() != ".json":
        raise LivingOdSameDayError("output path must end in .json")
    if output in (living, purpose):
        raise LivingOdSameDayError("input cannot also be output")
    for candidate in (output, part):
        if candidate.exists():
            raise LivingOdSameDayError(
                f"refusing to overwrite output or partial file: {candidate}"
            )
    return living, purpose, output, part


def run_living_od_same_day(
    *,
    living_population_path: Path,
    purpose_od_path: Path,
    output_path: Path,
    apply: bool = False,
) -> SameDayResult:
    living_path, purpose_path, output, part = _preflight(
        living_population_path, purpose_od_path, output_path
    )
    target_date = date.fromisoformat(LIVING_OD_SAME_DAY_DATE)
    od = _load_od_artifact(purpose_path)
    needed_hours = {
        adjacent
        for hour in LIVING_OD_SAME_DAY_HOURS
        for adjacent in (hour - 1, hour, hour + 1)
    }
    buckets: dict[tuple[str, int], _LivingBucket] = defaultdict(_LivingBucket)
    seen: set[tuple[date, int, str]] = set()
    source_rows = 0
    cell_ids: set[str] = set()
    cell_ids_by_hour: dict[int, set[str]] = defaultdict(set)
    cell_zone_by_id: dict[str, str] = {}
    all_codes: set[str] = set()
    for record in iter_living_population_csv(living_path):
        source_rows += 1
        if record.observed_date != target_date:
            raise LivingOdSameDayError(
                "living population input contains row outside fixed date: "
                f"{record.observed_date.isoformat()}"
            )
        identity = (record.observed_date, record.hour, record.cell_id)
        if identity in seen:
            raise LivingOdSameDayError(
                "duplicate living-population date/hour/cell: "
                f"{record.observed_date.isoformat()} {record.hour:02d} "
                f"{record.cell_id}"
            )
        seen.add(identity)
        prior_zone = cell_zone_by_id.setdefault(
            record.cell_id, record.administrative_dong_code
        )
        if prior_zone != record.administrative_dong_code:
            raise LivingOdSameDayError(
                "living-population cell changed administrative zone: "
                f"{record.cell_id} {prior_zone}->{record.administrative_dong_code}"
            )
        cell_ids.add(record.cell_id)
        cell_ids_by_hour[record.hour].add(record.cell_id)
        all_codes.add(record.administrative_dong_code)
        if record.hour not in needed_hours:
            continue
        bucket = buckets[(record.administrative_dong_code, record.hour)]
        bucket.rows += 1
        if record.total_population_masked:
            bucket.masked_rows += 1
        else:
            assert record.total_population is not None
            bucket.known_total += record.total_population
    if source_rows == 0:
        raise LivingOdSameDayError("living population input contains no rows")

    coverage_rows: list[dict[str, Any]] = []
    comparison_codes: dict[int, tuple[str, ...]] = {}
    for hour in LIVING_OD_SAME_DAY_HOURS:
        od_codes = {
            zone for zone, candidate_hour in od.movements if candidate_hour == hour
        }
        complete_lp_codes = {
            code
            for code in all_codes
            if all(
                (code, candidate_hour) in buckets
                for candidate_hour in (hour - 1, hour, hour + 1)
            )
        }
        exact = tuple(sorted(od_codes & complete_lp_codes))
        coverage = len(exact) / len(od_codes)
        coverage_rows.append(
            {
                "hour": hour,
                "od_zone_codes": len(od_codes),
                "living_population_complete_zone_codes": len(complete_lp_codes),
                "exact_intersection_zone_codes": len(exact),
                "code_coverage": round(coverage, 9),
            }
        )
        if coverage < LIVING_OD_SAME_DAY_CODE_COVERAGE_MIN:
            raise LivingOdSameDayError(
                f"hour {hour} exact code coverage {coverage:.9f} is below "
                f"{LIVING_OD_SAME_DAY_CODE_COVERAGE_MIN:.2f}"
            )
        comparison_codes[hour] = exact

    cell_universe_rows: list[dict[str, Any]] = []
    for hour in LIVING_OD_SAME_DAY_HOURS:
        comparisons: dict[str, float] = {}
        for label, left_hour, right_hour in (
            ("previous_to_current", hour - 1, hour),
            ("current_to_next", hour, hour + 1),
        ):
            left = cell_ids_by_hour[left_hour]
            right = cell_ids_by_hour[right_hour]
            union = left | right
            jaccard = len(left & right) / len(union) if union else 0.0
            comparisons[label] = jaccard
        minimum = min(comparisons.values())
        cell_universe_rows.append(
            {
                "hour": hour,
                "previous_to_current_jaccard": round(
                    comparisons["previous_to_current"], 9
                ),
                "current_to_next_jaccard": round(
                    comparisons["current_to_next"], 9
                ),
                "minimum_jaccard": round(minimum, 9),
            }
        )
        if minimum < LIVING_OD_SAME_DAY_CELL_UNIVERSE_JACCARD_MIN:
            raise LivingOdSameDayError(
                f"hour {hour} adjacent cell-universe Jaccard {minimum:.9f} is below "
                f"{LIVING_OD_SAME_DAY_CELL_UNIVERSE_JACCARD_MIN:.2f}"
            )

    living_summaries: dict[str, list[dict[str, Any]]] = {}
    correlation_by_imputation: dict[str, dict[str, Any]] = {}
    raw_primary_rhos: dict[float, list[float | None]] = {}
    for imputation in LIVING_OD_SAME_DAY_IMPUTATIONS:
        key = _imputation_key(imputation)
        hour_summaries: list[dict[str, Any]] = []
        for hour in sorted(needed_hours):
            hour_buckets = [
                bucket
                for (code, candidate_hour), bucket in buckets.items()
                if candidate_hour == hour
            ]
            rows = sum(bucket.rows for bucket in hour_buckets)
            masked_rows = sum(bucket.masked_rows for bucket in hour_buckets)
            hour_summaries.append(
                {
                    "hour": hour,
                    "total_population": round(
                        math.fsum(bucket.value(imputation) for bucket in hour_buckets),
                        6,
                    ),
                    "rows": rows,
                    "masked_rows": masked_rows,
                    "masked_row_ratio": (
                        round(masked_rows / rows, 9) if rows else None
                    ),
                }
            )
        living_summaries[key] = hour_summaries

        primary: list[dict[str, Any]] = []
        previous_delta: list[dict[str, Any]] = []
        gross_stock: list[dict[str, Any]] = []
        primary_rhos: list[float | None] = []
        for hour in LIVING_OD_SAME_DAY_HOURS:
            codes = comparison_codes[hour]
            movements = [od.movements[(code, hour)] for code in codes]
            stock_previous = [
                buckets[(code, hour - 1)].value(imputation) for code in codes
            ]
            stock = [buckets[(code, hour)].value(imputation) for code in codes]
            stock_next = [
                buckets[(code, hour + 1)].value(imputation) for code in codes
            ]
            net = [movement.net for movement in movements]
            gross = [movement.inbound + movement.outbound for movement in movements]
            primary_rho = _spearman(
                net,
                [right - left for left, right in zip(stock, stock_next, strict=True)],
            )
            previous_rho = _spearman(
                net,
                [right - left for left, right in zip(stock_previous, stock, strict=True)],
            )
            gross_rho = _spearman(gross, stock)
            primary_rhos.append(primary_rho)
            primary.append(
                {"hour": hour, "n": len(codes), "spearman_rho": _round_optional(primary_rho)}
            )
            previous_delta.append(
                {"hour": hour, "n": len(codes), "spearman_rho": _round_optional(previous_rho)}
            )
            gross_stock.append(
                {"hour": hour, "n": len(codes), "spearman_rho": _round_optional(gross_rho)}
            )
        raw_primary_rhos[imputation] = primary_rhos
        correlation_by_imputation[key] = {
            "verdict": _verdict(primary_rhos),
            "primary_net_vs_next_stock_delta": primary,
            "secondary_net_vs_previous_stock_delta": previous_delta,
            "secondary_gross_flow_vs_stock": gross_stock,
        }

    sensitivity_hours: list[dict[str, Any]] = []
    any_range_exceeded = False
    for index, hour in enumerate(LIVING_OD_SAME_DAY_HOURS):
        values = [raw_primary_rhos[value][index] for value in LIVING_OD_SAME_DAY_IMPUTATIONS]
        rho_range = (
            max(value for value in values if value is not None)
            - min(value for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
        exceeded = (
            rho_range is not None
            and rho_range > LIVING_OD_SAME_DAY_IMPUTATION_RHO_RANGE_MAX
        )
        any_range_exceeded = any_range_exceeded or exceeded
        sensitivity_hours.append(
            {
                "hour": hour,
                "rho_by_imputation": {
                    _imputation_key(value): _round_optional(raw_primary_rhos[value][index])
                    for value in LIVING_OD_SAME_DAY_IMPUTATIONS
                },
                "rho_range": _round_optional(rho_range),
                "range_exceeded": exceeded,
            }
        )
    verdicts = {
        _imputation_key(value): correlation_by_imputation[_imputation_key(value)][
            "verdict"
        ]
        for value in LIVING_OD_SAME_DAY_IMPUTATIONS
    }
    verdict_changed = len(set(verdicts.values())) > 1
    imputation_sensitive = any_range_exceeded or verdict_changed
    primary_key = _imputation_key(LIVING_OD_SAME_DAY_PRIMARY_IMPUTATION)
    base_verdict: Verdict = correlation_by_imputation[primary_key]["verdict"]
    final_verdict = _degrade(base_verdict) if imputation_sensitive else base_verdict

    thresholds = {
        "code_coverage_min": LIVING_OD_SAME_DAY_CODE_COVERAGE_MIN,
        "cell_universe_jaccard_min": LIVING_OD_SAME_DAY_CELL_UNIVERSE_JACCARD_MIN,
        "screening_median_min": LIVING_OD_SAME_DAY_SCREENING_MEDIAN_MIN,
        "conditional_median_min": LIVING_OD_SAME_DAY_CONDITIONAL_MEDIAN_MIN,
        "conditional_positive_min": LIVING_OD_SAME_DAY_CONDITIONAL_POSITIVE_MIN,
        "imputation_rho_range_max": LIVING_OD_SAME_DAY_IMPUTATION_RHO_RANGE_MAX,
    }
    report: dict[str, Any] = {
        "report_version": LIVING_OD_SAME_DAY_REPORT_VERSION,
        "scope": {
            "claim": "same-day cross-source relationship screen; not accuracy or causality",
            "public_model_effect": "none; offline shadow only",
            "date": LIVING_OD_SAME_DAY_DATE,
            "hours": list(LIVING_OD_SAME_DAY_HOURS),
            "zone_kind": SEOUL_ZONE_KIND,
            "fixed_od_zone_count": PURPOSE_OD_SEOUL_ZONE_COUNT,
            "rank_method": "average ties",
            "iid_p_values": None,
            "iid_p_values_reason": "spatially adjacent zones are not independent",
        },
        "thresholds": thresholds,
        "inputs": {
            "living_population": {
                "file": living_path.name,
                "size_bytes": living_path.stat().st_size,
                "sha256": _sha256(living_path),
                "encoding": "cp949",
                "source_rows": source_rows,
                "unique_cells": len(cell_ids),
                "administrative_zone_codes": len(all_codes),
            },
            "purpose_od_artifact": {
                "file": od.path.name,
                "size_bytes": od.path.stat().st_size,
                "artifact_sha256": od.sha256,
                "source_sha256": od.source_sha256,
                "source_schema_version": od.source_schema_version,
            },
        },
        "coverage": coverage_rows,
        "cell_universe_stability": cell_universe_rows,
        "living_population_by_imputation": living_summaries,
        "correlations_by_imputation": correlation_by_imputation,
        "imputation_sensitivity": {
            "primary_imputation": primary_key,
            "verdict_by_imputation": verdicts,
            "hours": sensitivity_hours,
            "verdict_changed": verdict_changed,
            "imputation_sensitive": imputation_sensitive,
        },
        "decision": {
            "base_verdict": base_verdict,
            "verdict": final_verdict,
            "imputation_sensitive": imputation_sensitive,
            "historical_feature_candidate": False,
            "accuracy_claim_allowed": False,
            "public_promotion_allowed": False,
        },
        "limitations": [
            "one-day cross-section is not predictive or causal evidence",
            "OA-22784 and OA-22300 are telecom-derived estimates with possible shared bias",
            "rolling-origin repetition and independent field labels remain required",
        ],
    }
    serialized = (
        json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if apply:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(part, output)
        finally:
            part.unlink(missing_ok=True)
    return SameDayResult(report=report, serialized=serialized, output_path=output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--living-population-csv", required=True, type=Path)
    parser.add_argument("--purpose-od-artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_living_od_same_day(
            living_population_path=args.living_population_csv,
            purpose_od_path=args.purpose_od_artifact,
            output_path=args.output,
            apply=args.apply,
        )
    except (LivingOdSameDayError, LivingPopulationCsvError, OSError) as exc:
        print(f"living/OD same-day screen failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "output": str(result.output_path),
                "report_sha256": hashlib.sha256(result.serialized).hexdigest(),
                "verdict": result.report["decision"]["verdict"],
                "imputation_sensitive": result.report["decision"][
                    "imputation_sensitive"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if not args.apply:
        print("dry-run: pass --apply to publish", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
