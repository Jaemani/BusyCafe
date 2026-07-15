#!/usr/bin/env python3
"""Run pre-registered held-out repetitions of the living/OD v2 screen.

Each explicit role/date pair reuses the complete same-day v2 evaluator.  The
three held-out Tuesdays alone determine the confirmatory gate; discovery and
single weekend dates remain descriptive.  This is offline relationship
evidence, not accuracy or independent ground truth.  Dry-run is default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any, Literal


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (  # noqa: E402
    LIVING_OD_REPEATS_CONDITIONAL_DAY_MIN,
    LIVING_OD_REPEATS_CONDITIONAL_POOLED_MEDIAN_MIN,
    LIVING_OD_REPEATS_CONDITIONAL_POSITIVE_RHO_MIN,
    LIVING_OD_REPEATS_DESCRIPTIVE_DATES,
    LIVING_OD_REPEATS_DISCOVERY_DATE,
    LIVING_OD_REPEATS_HELD_OUT_DATES,
    LIVING_OD_REPEATS_REPORT_VERSION,
    LIVING_OD_REPEATS_SUPPORTED_POOLED_MEDIAN_MIN,
    LIVING_OD_SAME_DAY_HOURS,
)
from app.ingest.living_population import LivingPopulationCsvError  # noqa: E402
from scripts import run_living_od_same_day as same_day  # noqa: E402


PART_SUFFIX = ".part"
PairRole = Literal["held_out", "discovery", "descriptive_only"]
RepeatVerdict = Literal["supported", "conditional", "not_supported"]


class LivingOdRepeatsError(ValueError):
    """Raised when pair inputs or reports violate the pre-registration."""


@dataclass(frozen=True, slots=True)
class RepeatPairInput:
    role: PairRole
    observed_date: date
    living_population_path: Path
    purpose_od_path: Path


@dataclass(frozen=True, slots=True)
class RepeatsResult:
    report: dict[str, Any]
    serialized: bytes
    output_path: Path


def _expected_roles() -> dict[date, PairRole]:
    expected: dict[date, PairRole] = {
        date.fromisoformat(value): "held_out"
        for value in LIVING_OD_REPEATS_HELD_OUT_DATES
    }
    expected[date.fromisoformat(LIVING_OD_REPEATS_DISCOVERY_DATE)] = "discovery"
    expected.update(
        {
            date.fromisoformat(value): "descriptive_only"
            for value in LIVING_OD_REPEATS_DESCRIPTIVE_DATES
        }
    )
    return expected


def _validate_pairs(pairs: Sequence[RepeatPairInput]) -> list[RepeatPairInput]:
    expected = _expected_roles()
    if len(pairs) != len(expected):
        raise LivingOdRepeatsError(
            f"exactly {len(expected)} pre-registered pair inputs are required"
        )
    by_date: dict[date, RepeatPairInput] = {}
    allowed_roles = {"held_out", "discovery", "descriptive_only"}
    for pair in pairs:
        if pair.role not in allowed_roles:
            raise LivingOdRepeatsError(f"unsupported pair role: {pair.role!r}")
        if not isinstance(pair.observed_date, date):
            raise LivingOdRepeatsError("pair observed_date must be a date")
        if pair.observed_date in by_date:
            raise LivingOdRepeatsError(
                f"duplicate pair date: {pair.observed_date.isoformat()}"
            )
        by_date[pair.observed_date] = pair
    if set(by_date) != set(expected):
        missing = sorted(value.isoformat() for value in set(expected) - set(by_date))
        extra = sorted(value.isoformat() for value in set(by_date) - set(expected))
        raise LivingOdRepeatsError(
            f"pair date set mismatch; missing={missing}, extra={extra}"
        )
    for observed_date, expected_role in expected.items():
        actual = by_date[observed_date].role
        if actual != expected_role:
            raise LivingOdRepeatsError(
                f"{observed_date.isoformat()} role must be {expected_role}; got {actual}"
            )
    return [by_date[value] for value in sorted(by_date)]


def _preflight(output_path: Path) -> tuple[Path, Path]:
    output = output_path.resolve()
    part = output.with_name(output.name + PART_SUFFIX)
    if output.suffix.lower() != ".json":
        raise LivingOdRepeatsError("output path must end in .json")
    for candidate in (output, part):
        if candidate.exists():
            raise LivingOdRepeatsError(
                f"refusing to overwrite output or partial file: {candidate}"
            )
    return output, part


def _degrade(verdict: RepeatVerdict) -> RepeatVerdict:
    if verdict == "supported":
        return "conditional"
    return "not_supported"


def run_living_od_repeats(
    *,
    pairs: Sequence[RepeatPairInput],
    output_path: Path,
    apply: bool = False,
) -> RepeatsResult:
    ordered_pairs = _validate_pairs(pairs)
    output, part = _preflight(output_path)

    pair_rows: list[dict[str, Any]] = []
    held_out_primary: list[float | None] = []
    held_out_base_verdicts: list[str] = []
    held_out_sensitive = False
    for pair in ordered_pairs:
        pair_output = output.with_name(
            f".{output.name}.{pair.observed_date:%Y%m%d}.pair.json"
        )
        result = same_day.run_living_od_same_day(
            living_population_path=pair.living_population_path,
            purpose_od_path=pair.purpose_od_path,
            output_path=pair_output,
            apply=False,
            target_date=pair.observed_date,
        )
        pair_report = result.report
        decision = pair_report["decision"]
        sensitivity = pair_report["imputation_sensitivity"]
        primary_variant = sensitivity["primary_variant"]
        primary_metrics = pair_report["correlations_by_variant"][primary_variant][
            "primary_net_vs_next_stock_delta"
        ]
        primary_rhos = [metric["spearman_rho"] for metric in primary_metrics]
        if len(primary_rhos) != len(LIVING_OD_SAME_DAY_HOURS):
            raise LivingOdRepeatsError("pair report has wrong primary metric count")
        if pair.role == "held_out":
            held_out_primary.extend(primary_rhos)
            held_out_base_verdicts.append(decision["base_verdict"])
            held_out_sensitive = held_out_sensitive or bool(
                decision["imputation_sensitive"]
            )
        pair_rows.append(
            {
                "role": pair.role,
                "date": pair.observed_date.isoformat(),
                "pair_report_sha256": hashlib.sha256(result.serialized).hexdigest(),
                "pair_report": pair_report,
            }
        )

    defined_primary = [
        float(value) for value in held_out_primary if value is not None
    ]
    all_nine_defined = len(defined_primary) == len(LIVING_OD_REPEATS_HELD_OUT_DATES) * len(
        LIVING_OD_SAME_DAY_HOURS
    )
    pooled_median = float(median(defined_primary)) if defined_primary else None
    pooled_minimum = min(defined_primary) if defined_primary else None
    positive_count = sum(value > 0 for value in defined_primary)
    conditional_or_better_days = sum(
        verdict in {"screening", "conditional"}
        for verdict in held_out_base_verdicts
    )
    supported = (
        all_nine_defined
        and all(verdict == "screening" for verdict in held_out_base_verdicts)
        and pooled_median is not None
        and pooled_median >= LIVING_OD_REPEATS_SUPPORTED_POOLED_MEDIAN_MIN
        and pooled_minimum is not None
        and pooled_minimum > 0
    )
    conditional = (
        all_nine_defined
        and conditional_or_better_days >= LIVING_OD_REPEATS_CONDITIONAL_DAY_MIN
        and pooled_median is not None
        and pooled_median >= LIVING_OD_REPEATS_CONDITIONAL_POOLED_MEDIAN_MIN
        and positive_count >= LIVING_OD_REPEATS_CONDITIONAL_POSITIVE_RHO_MIN
    )
    base_verdict: RepeatVerdict = (
        "supported" if supported else "conditional" if conditional else "not_supported"
    )
    final_verdict = _degrade(base_verdict) if held_out_sensitive else base_verdict

    report: dict[str, Any] = {
        "report_version": LIVING_OD_REPEATS_REPORT_VERSION,
        "scope": {
            "claim": "held-out same-day cross-source repeatability; not accuracy",
            "public_model_effect": "none; offline shadow only",
            "held_out_dates": list(LIVING_OD_REPEATS_HELD_OUT_DATES),
            "discovery_date": LIVING_OD_REPEATS_DISCOVERY_DATE,
            "descriptive_dates": list(LIVING_OD_REPEATS_DESCRIPTIVE_DATES),
            "hours": list(LIVING_OD_SAME_DAY_HOURS),
            "discovery_in_verdict": False,
            "descriptive_in_verdict": False,
            "source_release_independent_samples": False,
        },
        "thresholds": {
            "supported_all_held_out_single_day_verdict": "screening",
            "supported_pooled_median_min": (
                LIVING_OD_REPEATS_SUPPORTED_POOLED_MEDIAN_MIN
            ),
            "supported_pooled_minimum_strictly_positive": True,
            "conditional_day_min": LIVING_OD_REPEATS_CONDITIONAL_DAY_MIN,
            "conditional_single_day_verdicts": ["screening", "conditional"],
            "conditional_pooled_median_min": (
                LIVING_OD_REPEATS_CONDITIONAL_POOLED_MEDIAN_MIN
            ),
            "conditional_positive_rho_min": (
                LIVING_OD_REPEATS_CONDITIONAL_POSITIVE_RHO_MIN
            ),
            "imputation_sensitive_degradation_steps": 1,
        },
        "pairs": pair_rows,
        "held_out_summary": {
            "single_day_base_verdicts": held_out_base_verdicts,
            "primary_rhos": held_out_primary,
            "primary_rho_count": len(held_out_primary),
            "all_primary_rhos_defined": all_nine_defined,
            "pooled_median": (
                round(pooled_median, 9) if pooled_median is not None else None
            ),
            "pooled_minimum": (
                round(pooled_minimum, 9) if pooled_minimum is not None else None
            ),
            "positive_rho_count": positive_count,
            "conditional_or_better_day_count": conditional_or_better_days,
            "imputation_sensitive": held_out_sensitive,
        },
        "decision": {
            "base_verdict": base_verdict,
            "verdict": final_verdict,
            "imputation_sensitive": held_out_sensitive,
            "historical_feature_candidate": False,
            "accuracy_claim_allowed": False,
            "public_promotion_allowed": False,
        },
        "limitations": [
            (
                "all dates from one monthly living-population source release "
                "are not independent releases"
            ),
            "OA-22784 and OA-22300 are telecom-derived estimates with possible shared bias",
            "weekend dates are descriptive singletons and cannot support generalization",
            "other-month rolling-origin and independent Phase 6 labels remain required",
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
    return RepeatsResult(report=report, serialized=serialized, output_path=output)


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("date must be canonical YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must be canonical YYYY-MM-DD")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair",
        action="append",
        nargs=4,
        required=True,
        metavar=("ROLE", "DATE", "LIVING_CSV", "OD_ARTIFACT"),
        help="repeat for each exact pre-registered role/date pair",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        pairs = [
            RepeatPairInput(
                role=raw[0],
                observed_date=_parse_date(raw[1]),
                living_population_path=Path(raw[2]),
                purpose_od_path=Path(raw[3]),
            )
            for raw in args.pair
        ]
        result = run_living_od_repeats(
            pairs=pairs,
            output_path=args.output,
            apply=args.apply,
        )
    except (
        argparse.ArgumentTypeError,
        LivingOdRepeatsError,
        same_day.LivingOdSameDayError,
        LivingPopulationCsvError,
        OSError,
    ) as exc:
        print(f"living/OD held-out repeats failed: {exc}", file=sys.stderr)
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
