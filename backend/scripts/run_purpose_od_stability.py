#!/usr/bin/env python3
"""Evaluate repeatability of historical purpose-OD shadow artifacts.

This is a structural pilot, not an accuracy evaluation.  It compares adjacent
weekly artifacts for the same ISO weekday and reports whether scalar flow,
purpose mix, and coarse centroid-to-centroid direction repeat.  Descriptive
weekend/holiday inputs are summarized but never enter the weekly verdict.

No network or database is used.  Dry-run is the default; ``--apply`` publishes
one deterministic JSON report without overwriting an existing file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (  # noqa: E402
    PURPOSE_OD_HASH_CHUNK_BYTES,
    PURPOSE_OD_SEOUL_ZONE_COUNT,
    PURPOSE_OD_SHADOW_MODEL_VERSION,
    PURPOSE_OD_STABILITY_DECILE_FRACTION,
    PURPOSE_OD_STABILITY_DECILE_JACCARD_MIN,
    PURPOSE_OD_STABILITY_FLOW_MEDIAN_MIN,
    PURPOSE_OD_STABILITY_HOURS,
    PURPOSE_OD_STABILITY_MIN_WEEKLY_DATES,
    PURPOSE_OD_STABILITY_NET_CONDITIONAL_MEDIAN_MIN,
    PURPOSE_OD_STABILITY_NET_CONDITIONAL_MIN,
    PURPOSE_OD_STABILITY_NET_MEDIAN_MIN,
    PURPOSE_OD_STABILITY_NET_MIN_MIN,
    PURPOSE_OD_STABILITY_PURPOSE_JSD_MEDIAN_MAX,
    PURPOSE_OD_STABILITY_PURPOSE_JSD_P90_MAX,
    PURPOSE_OD_STABILITY_REPORT_VERSION,
    PURPOSE_OD_STABILITY_VECTOR_ANGLE_MEDIAN_MAX_DEG,
    PURPOSE_OD_STABILITY_VECTOR_ANGLE_P90_MAX_DEG,
    PURPOSE_OD_STABILITY_VECTOR_COVERAGE_MIN,
    PURPOSE_OD_STABILITY_VECTOR_ELIGIBLE_RATIO_MIN,
    PURPOSE_OD_STABILITY_VECTOR_STRENGTH_DELTA_MEDIAN_MAX,
    PURPOSE_OD_STABILITY_VECTOR_STRENGTH_MIN,
    PURPOSE_OD_STABILITY_VECTOR_WITHIN_45_MIN,
)


SEOUL_ZONE_KIND = "seoul_admin_dong"
PART_SUFFIX = ".part"
PURPOSES = tuple(str(value) for value in range(1, 8))


class PurposeOdStabilityError(ValueError):
    """Raised when artifacts cannot support the pre-registered comparison."""


@dataclass(frozen=True, slots=True)
class Movement:
    zone_code: str
    hour: int
    inbound: float
    outbound: float
    net: float
    purpose_counts: tuple[tuple[str, float], ...]
    direction_heading_deg: float | None
    direction_strength: float | None
    direction_coverage: float


@dataclass(frozen=True, slots=True)
class Artifact:
    path: Path
    sha256: str
    observed_date: date
    source_sha256: str
    source_schema_version: str
    centroid_sha256: str
    centroid_schema_version: str
    movements: dict[tuple[str, int], Movement]


@dataclass(frozen=True, slots=True)
class StabilityResult:
    report: dict[str, Any]
    serialized: bytes
    output_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(PURPOSE_OD_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _dict(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PurposeOdStabilityError(f"{field} must be an object")
    return value


def _list(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise PurposeOdStabilityError(f"{field} must be an array")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PurposeOdStabilityError(f"{field} must be canonical non-empty text")
    return value


def _number(value: object, *, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PurposeOdStabilityError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise PurposeOdStabilityError(f"{field} has invalid numeric value")
    return number


def _optional_number(value: object, *, field: str) -> float | None:
    return None if value is None else _number(value, field=field)


def _iso_date(value: object, *, field: str) -> date:
    text = _text(value, field=field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise PurposeOdStabilityError(f"{field} must be an ISO date") from None
    if parsed.isoformat() != text:
        raise PurposeOdStabilityError(f"{field} must be a canonical ISO date")
    return parsed


def _load_artifact(path: Path) -> Artifact:
    resolved = path.resolve()
    if not resolved.is_file():
        raise PurposeOdStabilityError(f"artifact does not exist: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PurposeOdStabilityError(f"cannot read artifact {resolved}: {exc}") from exc
    root = _dict(payload, field="artifact root")
    artifact_meta = _dict(root.get("artifact"), field="artifact")
    if artifact_meta.get("model_version") != PURPOSE_OD_SHADOW_MODEL_VERSION:
        raise PurposeOdStabilityError(f"model_version mismatch: {resolved}")
    if artifact_meta.get("public_model_effect") != "none; offline shadow only":
        raise PurposeOdStabilityError("artifact must remain offline shadow only")

    target = _dict(root.get("target"), field="target")
    observed_date = _iso_date(target.get("date"), field="target.date")
    raw_hours = _list(target.get("hours"), field="target.hours")
    if raw_hours != list(PURPOSE_OD_STABILITY_HOURS):
        raise PurposeOdStabilityError(
            f"target.hours must equal {list(PURPOSE_OD_STABILITY_HOURS)}"
        )

    source = _dict(root.get("source"), field="source")
    source_sha256 = _text(source.get("sha256"), field="source.sha256")
    source_schema = _text(
        source.get("schema_version"), field="source.schema_version"
    )
    centroids = _dict(root.get("centroids"), field="centroids")
    centroid_sha256 = _text(centroids.get("sha256"), field="centroids.sha256")
    centroid_schema = _text(
        centroids.get("schema_version"), field="centroids.schema_version"
    )
    coverage = _dict(root.get("coverage"), field="coverage")
    if _number(
        coverage.get("centroid_code_ratio"),
        field="coverage.centroid_code_ratio",
    ) != 1.0:
        raise PurposeOdStabilityError("centroid_code_ratio must equal 1.0")
    if coverage.get("missing_origin_codes") != []:
        raise PurposeOdStabilityError("missing_origin_codes must be empty")
    if coverage.get("missing_destination_codes") != []:
        raise PurposeOdStabilityError("missing_destination_codes must be empty")

    movements: dict[tuple[str, int], Movement] = {}
    for index, raw in enumerate(_list(root.get("movements"), field="movements")):
        movement = _dict(raw, field=f"movements[{index}]")
        if movement.get("zone_kind") != SEOUL_ZONE_KIND:
            continue
        zone_code = _text(
            movement.get("administrative_zone_code"),
            field=f"movements[{index}].administrative_zone_code",
        )
        raw_hour = movement.get("hour")
        if isinstance(raw_hour, bool) or not isinstance(raw_hour, int):
            raise PurposeOdStabilityError("movement hour must be an integer")
        if raw_hour not in PURPOSE_OD_STABILITY_HOURS:
            raise PurposeOdStabilityError("movement hour outside fixed pilot hours")
        key = (zone_code, raw_hour)
        if key in movements:
            raise PurposeOdStabilityError(f"duplicate movement key {key}")
        purpose_raw = _dict(
            movement.get("purpose_estimated_counts"),
            field=f"movements[{index}].purpose_estimated_counts",
        )
        if any(purpose not in PURPOSES for purpose in purpose_raw):
            raise PurposeOdStabilityError("unknown purpose code")
        purpose_counts = tuple(
            (
                purpose,
                _number(
                    purpose_raw.get(purpose, 0.0),
                    field=f"purpose_estimated_counts.{purpose}",
                    minimum=0.0,
                ),
            )
            for purpose in PURPOSES
        )
        vector = _dict(
            movement.get("movement_vector"),
            field=f"movements[{index}].movement_vector",
        )
        direction_coverage = _number(
            vector.get("eligible_estimated_count_coverage"),
            field="movement_vector.eligible_estimated_count_coverage",
            minimum=0.0,
        )
        if direction_coverage > 1.0:
            raise PurposeOdStabilityError("direction coverage must not exceed 1")
        movements[key] = Movement(
            zone_code=zone_code,
            hour=raw_hour,
            inbound=_number(
                movement.get("inbound_estimated_count"),
                field="inbound_estimated_count",
                minimum=0.0,
            ),
            outbound=_number(
                movement.get("outbound_estimated_count"),
                field="outbound_estimated_count",
                minimum=0.0,
            ),
            net=_number(movement.get("net_estimated_count"), field="net"),
            purpose_counts=purpose_counts,
            direction_heading_deg=_optional_number(
                vector.get("travel_heading_deg"), field="travel_heading_deg"
            ),
            direction_strength=_optional_number(
                vector.get("direction_strength"), field="direction_strength"
            ),
            direction_coverage=direction_coverage,
        )

    zones = {zone for zone, _ in movements}
    if len(zones) != PURPOSE_OD_SEOUL_ZONE_COUNT:
        raise PurposeOdStabilityError(
            f"expected {PURPOSE_OD_SEOUL_ZONE_COUNT} Seoul zones, got {len(zones)}"
        )
    for hour in PURPOSE_OD_STABILITY_HOURS:
        hour_zones = {zone for zone, candidate_hour in movements if candidate_hour == hour}
        if hour_zones != zones:
            raise PurposeOdStabilityError(f"incomplete Seoul zone universe at {hour}")
    return Artifact(
        path=resolved,
        sha256=_sha256(resolved),
        observed_date=observed_date,
        source_sha256=source_sha256,
        source_schema_version=source_schema,
        centroid_sha256=centroid_sha256,
        centroid_schema_version=centroid_schema,
        movements=movements,
    )


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index, _ in ordered[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_rank = _average_ranks(left)
    right_rank = _average_ranks(right)
    left_mean = sum(left_rank) / len(left_rank)
    right_mean = sum(right_rank) / len(right_rank)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_rank, right_rank, strict=True)
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left_rank))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right_rank))
    if left_norm == 0 or right_norm == 0:
        return None
    return numerator / (left_norm * right_norm)


def _quantile(values: Sequence[float], probability: float) -> float | None:
    """Return deterministic linear (R-7) quantile."""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _required_quantile(values: Sequence[float], probability: float) -> float:
    result = _quantile(values, probability)
    if result is None:
        raise PurposeOdStabilityError("quantile requires at least one value")
    return result


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _rank_tail(zones: Sequence[str], values: Sequence[float], *, high: bool) -> set[str]:
    count = math.ceil(len(zones) * PURPOSE_OD_STABILITY_DECILE_FRACTION)
    ordered = sorted(
        zip(zones, values, strict=True),
        key=lambda item: ((-item[1] if high else item[1]), item[0]),
    )
    return {zone for zone, _ in ordered[:count]}


def _distribution(counts: Sequence[float]) -> tuple[float, ...]:
    total = sum(counts)
    if total <= 0:
        raise PurposeOdStabilityError("purpose distribution has no mass")
    return tuple(value / total for value in counts)


def _js_distance(left: Sequence[float], right: Sequence[float]) -> float:
    middle = tuple((a + b) / 2.0 for a, b in zip(left, right, strict=True))

    def divergence(values: Sequence[float]) -> float:
        return sum(
            value * math.log2(value / center)
            for value, center in zip(values, middle, strict=True)
            if value > 0
        )

    return math.sqrt(0.5 * divergence(left) + 0.5 * divergence(right))


def _angle_difference(left: float, right: float) -> float:
    raw = abs(left - right) % 360.0
    return min(raw, 360.0 - raw)


def _purpose_distribution(artifact: Artifact, *, hour: int) -> tuple[float, ...]:
    totals = [0.0] * len(PURPOSES)
    for (zone, candidate_hour), movement in artifact.movements.items():
        if candidate_hour != hour:
            continue
        del zone
        for index, (_, count) in enumerate(movement.purpose_counts):
            totals[index] += count
    return _distribution(totals)


def _pair_metrics(left: Artifact, right: Artifact, *, hour: int) -> dict[str, Any]:
    zones = sorted(zone for zone, candidate_hour in left.movements if candidate_hour == hour)
    if zones != sorted(
        zone for zone, candidate_hour in right.movements if candidate_hour == hour
    ):
        raise PurposeOdStabilityError("weekly artifacts have different zone universe")
    left_rows = [left.movements[(zone, hour)] for zone in zones]
    right_rows = [right.movements[(zone, hour)] for zone in zones]

    left_net = [row.net for row in left_rows]
    right_net = [row.net for row in right_rows]
    net_spearman = _spearman(left_net, right_net)
    inbound_spearman = _spearman(
        [row.inbound for row in left_rows], [row.inbound for row in right_rows]
    )
    outbound_spearman = _spearman(
        [row.outbound for row in left_rows], [row.outbound for row in right_rows]
    )
    if (
        net_spearman is None
        or inbound_spearman is None
        or outbound_spearman is None
    ):
        raise PurposeOdStabilityError("rank correlation undefined for weekly pair")

    directions: list[tuple[float, float, float, float]] = []
    for left_row, right_row in zip(left_rows, right_rows, strict=True):
        if (
            left_row.direction_coverage >= PURPOSE_OD_STABILITY_VECTOR_COVERAGE_MIN
            and right_row.direction_coverage >= PURPOSE_OD_STABILITY_VECTOR_COVERAGE_MIN
            and left_row.direction_strength is not None
            and right_row.direction_strength is not None
            and left_row.direction_strength >= PURPOSE_OD_STABILITY_VECTOR_STRENGTH_MIN
            and right_row.direction_strength >= PURPOSE_OD_STABILITY_VECTOR_STRENGTH_MIN
            and left_row.direction_heading_deg is not None
            and right_row.direction_heading_deg is not None
        ):
            directions.append(
                (
                    left_row.direction_heading_deg,
                    right_row.direction_heading_deg,
                    left_row.direction_strength,
                    right_row.direction_strength,
                )
            )
    angle_differences = [_angle_difference(a, b) for a, b, _, _ in directions]
    strength_differences = [abs(a - b) for _, _, a, b in directions]
    purpose_left = _purpose_distribution(left, hour=hour)
    purpose_right = _purpose_distribution(right, hour=hour)
    return {
        "left_date": left.observed_date.isoformat(),
        "right_date": right.observed_date.isoformat(),
        "hour": hour,
        "net_spearman": round(float(net_spearman), 9),
        "inbound_spearman": round(float(inbound_spearman), 9),
        "outbound_spearman": round(float(outbound_spearman), 9),
        "top_decile_jaccard": round(
            _jaccard(
                _rank_tail(zones, left_net, high=True),
                _rank_tail(zones, right_net, high=True),
            ),
            9,
        ),
        "bottom_decile_jaccard": round(
            _jaccard(
                _rank_tail(zones, left_net, high=False),
                _rank_tail(zones, right_net, high=False),
            ),
            9,
        ),
        "purpose_js_distance": round(_js_distance(purpose_left, purpose_right), 9),
        "purpose_7_ratio_left": round(purpose_left[6], 9),
        "purpose_7_ratio_right": round(purpose_right[6], 9),
        "vector": {
            "eligible_zones": len(directions),
            "eligible_ratio": round(len(directions) / len(zones), 9),
            "angle_median_deg": (
                round(float(median(angle_differences)), 9)
                if angle_differences
                else None
            ),
            "angle_p90_deg": (
                round(_required_quantile(angle_differences, 0.90), 9)
                if angle_differences
                else None
            ),
            "within_45_ratio": (
                round(
                    sum(value <= 45.0 for value in angle_differences)
                    / len(angle_differences),
                    9,
                )
                if angle_differences
                else None
            ),
            "strength_delta_median": (
                round(float(median(strength_differences)), 9)
                if strength_differences
                else None
            ),
        },
    }


def _summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "minimum": round(min(values), 9),
        "median": round(float(median(values)), 9),
        "p90": round(_required_quantile(values, 0.90), 9),
        "maximum": round(max(values), 9),
    }


def _descriptive(artifact: Artifact) -> dict[str, Any]:
    hours: list[dict[str, Any]] = []
    for hour in PURPOSE_OD_STABILITY_HOURS:
        rows = [
            movement
            for (_, candidate_hour), movement in artifact.movements.items()
            if candidate_hour == hour
        ]
        purposes = _purpose_distribution(artifact, hour=hour)
        hours.append(
            {
                "hour": hour,
                "inbound": round(sum(row.inbound for row in rows), 6),
                "outbound": round(sum(row.outbound for row in rows), 6),
                "net": round(sum(row.net for row in rows), 6),
                "purpose_ratios": {
                    purpose: round(value, 9)
                    for purpose, value in zip(PURPOSES, purposes, strict=True)
                },
            }
        )
    return {"date": artifact.observed_date.isoformat(), "hours": hours}


def _preflight(
    weekly_paths: Sequence[Path],
    descriptive_paths: Sequence[Path],
    output_path: Path,
) -> tuple[list[Path], list[Path], Path, Path]:
    weekly = [path.resolve() for path in weekly_paths]
    descriptive = [path.resolve() for path in descriptive_paths]
    all_paths = weekly + descriptive
    if len(weekly) < PURPOSE_OD_STABILITY_MIN_WEEKLY_DATES:
        raise PurposeOdStabilityError(
            f"at least {PURPOSE_OD_STABILITY_MIN_WEEKLY_DATES} weekly artifacts required"
        )
    if len(set(all_paths)) != len(all_paths):
        raise PurposeOdStabilityError("artifact paths must be unique")
    output = output_path.resolve()
    part = output.with_name(output.name + PART_SUFFIX)
    if output.suffix.lower() != ".json":
        raise PurposeOdStabilityError("output path must end in .json")
    if output in all_paths:
        raise PurposeOdStabilityError("input cannot also be output")
    for candidate in (output, part):
        if candidate.exists():
            raise PurposeOdStabilityError(
                f"refusing to overwrite output or partial file: {candidate}"
            )
    return weekly, descriptive, output, part


def run_stability_pilot(
    *,
    weekly_paths: Sequence[Path],
    descriptive_paths: Sequence[Path],
    output_path: Path,
    apply: bool = False,
) -> StabilityResult:
    weekly_sources, descriptive_sources, output, part = _preflight(
        weekly_paths, descriptive_paths, output_path
    )
    weekly = sorted((_load_artifact(path) for path in weekly_sources), key=lambda a: a.observed_date)
    descriptive = sorted(
        (_load_artifact(path) for path in descriptive_sources),
        key=lambda a: a.observed_date,
    )
    dates = [artifact.observed_date for artifact in weekly]
    if len(set(dates)) != len(dates):
        raise PurposeOdStabilityError("weekly dates must be unique")
    weekday = dates[0].isoweekday()
    if any(value.isoweekday() != weekday for value in dates):
        raise PurposeOdStabilityError("weekly artifacts must share one ISO weekday")
    if any(right - left != timedelta(days=7) for left, right in zip(dates, dates[1:])):
        raise PurposeOdStabilityError("weekly artifacts must be adjacent seven-day dates")
    all_artifacts = [*weekly, *descriptive]
    if len({artifact.observed_date for artifact in all_artifacts}) != len(all_artifacts):
        raise PurposeOdStabilityError("artifact dates must be unique")
    centroid_contracts = {
        (artifact.centroid_sha256, artifact.centroid_schema_version)
        for artifact in all_artifacts
    }
    source_schemas = {artifact.source_schema_version for artifact in all_artifacts}
    if len(centroid_contracts) != 1 or len(source_schemas) != 1:
        raise PurposeOdStabilityError("artifacts must share centroid/source schema")

    pairs = [
        _pair_metrics(left, right, hour=hour)
        for left, right in zip(weekly, weekly[1:])
        for hour in PURPOSE_OD_STABILITY_HOURS
    ]
    net = [item["net_spearman"] for item in pairs]
    inbound = [item["inbound_spearman"] for item in pairs]
    outbound = [item["outbound_spearman"] for item in pairs]
    top_jaccard = [item["top_decile_jaccard"] for item in pairs]
    bottom_jaccard = [item["bottom_decile_jaccard"] for item in pairs]
    purpose_jsd = [item["purpose_js_distance"] for item in pairs]

    scalar_supported = (
        median(net) >= PURPOSE_OD_STABILITY_NET_MEDIAN_MIN
        and min(net) >= PURPOSE_OD_STABILITY_NET_MIN_MIN
        and median(inbound) >= PURPOSE_OD_STABILITY_FLOW_MEDIAN_MIN
        and median(outbound) >= PURPOSE_OD_STABILITY_FLOW_MEDIAN_MIN
        and median(top_jaccard) >= PURPOSE_OD_STABILITY_DECILE_JACCARD_MIN
        and median(bottom_jaccard) >= PURPOSE_OD_STABILITY_DECILE_JACCARD_MIN
    )
    scalar_conditional = (
        median(net) >= PURPOSE_OD_STABILITY_NET_CONDITIONAL_MEDIAN_MIN
        and min(net) >= PURPOSE_OD_STABILITY_NET_CONDITIONAL_MIN
    )
    scalar_verdict = (
        "supported"
        if scalar_supported
        else "conditional" if scalar_conditional else "not_supported"
    )
    purpose_verdict = (
        "stable"
        if median(purpose_jsd) <= PURPOSE_OD_STABILITY_PURPOSE_JSD_MEDIAN_MAX
        and _required_quantile(purpose_jsd, 0.90)
        <= PURPOSE_OD_STABILITY_PURPOSE_JSD_P90_MAX
        else "not_stable"
    )

    vector_rows = [item["vector"] for item in pairs]
    eligible_ratios = [item["eligible_ratio"] for item in vector_rows]
    angle_medians = [
        item["angle_median_deg"]
        for item in vector_rows
        if item["angle_median_deg"] is not None
    ]
    angle_p90s = [
        item["angle_p90_deg"]
        for item in vector_rows
        if item["angle_p90_deg"] is not None
    ]
    within_45 = [
        item["within_45_ratio"]
        for item in vector_rows
        if item["within_45_ratio"] is not None
    ]
    strength_deltas = [
        item["strength_delta_median"]
        for item in vector_rows
        if item["strength_delta_median"] is not None
    ]
    vector_usable = (
        len(angle_medians) == len(pairs)
        and min(eligible_ratios) >= PURPOSE_OD_STABILITY_VECTOR_ELIGIBLE_RATIO_MIN
        and max(angle_medians)
        <= PURPOSE_OD_STABILITY_VECTOR_ANGLE_MEDIAN_MAX_DEG
        and max(angle_p90s) <= PURPOSE_OD_STABILITY_VECTOR_ANGLE_P90_MAX_DEG
        and min(within_45) >= PURPOSE_OD_STABILITY_VECTOR_WITHIN_45_MIN
        and max(strength_deltas)
        <= PURPOSE_OD_STABILITY_VECTOR_STRENGTH_DELTA_MEDIAN_MAX
    )

    thresholds = {
        name.removeprefix("PURPOSE_OD_STABILITY_").lower(): value
        for name, value in globals().items()
        if name.startswith("PURPOSE_OD_STABILITY_")
        and name
        not in {
            "PURPOSE_OD_STABILITY_REPORT_VERSION",
            "PURPOSE_OD_STABILITY_HOURS",
        }
    }
    report: dict[str, Any] = {
        "report_version": PURPOSE_OD_STABILITY_REPORT_VERSION,
        "scope": {
            "claim": "weekly structural repeatability; not accuracy",
            "public_model_effect": "none; offline shadow only",
            "hours": list(PURPOSE_OD_STABILITY_HOURS),
            "zone_kind": SEOUL_ZONE_KIND,
            "zone_count": PURPOSE_OD_SEOUL_ZONE_COUNT,
            "weekly_iso_weekday": weekday,
            "quantile_method": "linear R-7",
        },
        "thresholds": thresholds,
        "inputs": [
            {
                "date": artifact.observed_date.isoformat(),
                "role": "weekly" if artifact in weekly else "descriptive_only",
                "file": artifact.path.name,
                "artifact_sha256": artifact.sha256,
                "source_sha256": artifact.source_sha256,
                "source_schema_version": artifact.source_schema_version,
                "centroid_sha256": artifact.centroid_sha256,
                "centroid_schema_version": artifact.centroid_schema_version,
            }
            for artifact in sorted(all_artifacts, key=lambda item: item.observed_date)
        ],
        "weekly_pairs": pairs,
        "weekly_summary": {
            "scalar": {
                "verdict": scalar_verdict,
                "net_spearman": _summary(net),
                "inbound_spearman": _summary(inbound),
                "outbound_spearman": _summary(outbound),
                "top_decile_jaccard": _summary(top_jaccard),
                "bottom_decile_jaccard": _summary(bottom_jaccard),
            },
            "purpose": {
                "verdict": purpose_verdict,
                "js_distance": _summary(purpose_jsd),
                "purpose_7_ratio_min": round(
                    min(
                        min(item["purpose_7_ratio_left"], item["purpose_7_ratio_right"])
                        for item in pairs
                    ),
                    9,
                ),
                "purpose_7_ratio_max": round(
                    max(
                        max(item["purpose_7_ratio_left"], item["purpose_7_ratio_right"])
                        for item in pairs
                    ),
                    9,
                ),
            },
            "vector": {
                "verdict": "usable" if vector_usable else "not_usable",
                "eligible_ratio": _summary(eligible_ratios),
                "angle_median_deg": _summary(angle_medians) if angle_medians else None,
                "angle_p90_deg": _summary(angle_p90s) if angle_p90s else None,
                "within_45_ratio": _summary(within_45) if within_45 else None,
                "strength_delta_median": (
                    _summary(strength_deltas) if strength_deltas else None
                ),
            },
        },
        "descriptive_only": [_descriptive(artifact) for artifact in descriptive],
        "decision": {
            "historical_scalar_prior_candidate": scalar_verdict == "supported",
            "purpose_feature_candidate": purpose_verdict == "stable",
            "vector_feature_candidate": vector_usable,
            "accuracy_claim_allowed": False,
            "public_promotion_allowed": False,
        },
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
    return StabilityResult(report=report, serialized=serialized, output_path=output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weekly-artifact", action="append", required=True, type=Path)
    parser.add_argument("--descriptive-artifact", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_stability_pilot(
            weekly_paths=args.weekly_artifact,
            descriptive_paths=args.descriptive_artifact,
            output_path=args.output,
            apply=args.apply,
        )
    except (OSError, PurposeOdStabilityError) as exc:
        print(f"purpose OD stability failed: {exc}", file=sys.stderr)
        return 1
    summary = result.report["weekly_summary"]
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "output": str(result.output_path),
                "sha256": hashlib.sha256(result.serialized).hexdigest(),
                "scalar_verdict": summary["scalar"]["verdict"],
                "purpose_verdict": summary["purpose"]["verdict"],
                "vector_verdict": summary["vector"]["verdict"],
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
