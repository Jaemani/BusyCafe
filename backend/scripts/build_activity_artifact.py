#!/usr/bin/env python3
"""Build a deterministic offline 250m-cell activity GeoJSON artifact.

The input is the compact living-population Parquet contract.  A target date
and hour are always explicit.  Baselines receive only rows strictly before
the target date, so a target observation can never leak into its own normal.

This tool performs no network, database, or public-API work.  It is dry-run by
default and publishes one GeoJSON file only when ``--apply`` is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (  # noqa: E402
    LIVING_POPULATION_COMPACT_PART_SUFFIX,
    LIVING_POPULATION_COMPACT_SCHEMA_VERSION,
    LIVING_POPULATION_HASH_CHUNK_BYTES,
)
from app.ingest.national_grid import (  # noqa: E402
    CELL_GEOMETRY_VERSION,
    cell_wgs84_corners,
)
from app.scoring.activity_shadow import (  # noqa: E402
    ActivityBaselineReference,
    ActivityContributorInput,
    ActivityShadowEstimate,
    calculate_activity_shadow,
)
from app.scoring.temporal_baseline_shadow import (  # noqa: E402
    HistoricalCellObservation,
    TemporalBaselineEstimate,
    estimate_temporal_baseline_shadow,
)


ARTIFACT_MODEL_VERSION = "v1-offline-cell-activity-artifact"
SOURCE_ID = "seoul-living-population-oa-22784"
OBSERVATION_TYPE = "presence_count"
SEOUL_TIMEZONE = "Asia/Seoul"
REQUIRED_PARQUET_COLUMNS = frozenset(
    {"date", "hour", "cell_id", "total", "masked", "source_file"}
)


class ActivityArtifactError(ValueError):
    """Raised when an artifact cannot be built without inventing evidence."""


@dataclass(frozen=True, slots=True)
class CalendarSpec:
    version: str
    timezone: str
    public_holidays: frozenset[date]
    sha256: str
    source_file: str


@dataclass(frozen=True, slots=True)
class _SourceRow:
    observed_date: date
    hour: int
    cell_id: str
    total: float | None
    masked: bool
    source_file: str


@dataclass(frozen=True, slots=True)
class ActivityArtifactResult:
    artifact: dict[str, Any]
    output_path: Path
    serialized: bytes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(LIVING_POPULATION_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_iso_date(raw: object, *, field: str) -> date:
    if not isinstance(raw, str):
        raise ActivityArtifactError(f"{field} must contain ISO date strings")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise ActivityArtifactError(f"invalid ISO date in {field}: {raw!r}") from None
    if parsed.isoformat() != raw:
        raise ActivityArtifactError(f"non-canonical ISO date in {field}: {raw!r}")
    return parsed


def _load_calendar(path: Path) -> CalendarSpec:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ActivityArtifactError(f"calendar does not exist: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivityArtifactError(
            f"cannot read calendar JSON {resolved}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ActivityArtifactError("calendar JSON must be an object")
    version = payload.get("version")
    timezone = payload.get("timezone")
    holidays_raw = payload.get("public_holidays")
    if not isinstance(version, str) or not version.strip():
        raise ActivityArtifactError("calendar version must be non-empty")
    if timezone != SEOUL_TIMEZONE:
        raise ActivityArtifactError(
            f"calendar timezone must be explicit {SEOUL_TIMEZONE!r}"
        )
    if not isinstance(holidays_raw, list):
        raise ActivityArtifactError("calendar public_holidays must be an explicit list")
    holidays = [_parse_iso_date(item, field="public_holidays") for item in holidays_raw]
    if len(set(holidays)) != len(holidays):
        raise ActivityArtifactError(
            "calendar public_holidays must not contain duplicates"
        )
    return CalendarSpec(
        version=version.strip(),
        timezone=timezone,
        public_holidays=frozenset(holidays),
        sha256=_sha256(resolved),
        source_file=resolved.name,
    )


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _preflight(
    inputs: list[Path], output_path: Path
) -> tuple[list[Path], Path, Path]:
    resolved_inputs = sorted({path.resolve() for path in inputs}, key=str)
    if not resolved_inputs:
        raise ActivityArtifactError("at least one Parquet input is required")
    if len(resolved_inputs) != len(inputs):
        raise ActivityArtifactError("Parquet input paths must be unique")
    for path in resolved_inputs:
        if not path.is_file():
            raise ActivityArtifactError(f"Parquet input does not exist: {path}")
        if path.suffix.lower() != ".parquet":
            raise ActivityArtifactError(f"input must end in .parquet: {path}")
    output = output_path.resolve()
    if output.suffix.lower() not in {".json", ".geojson"}:
        raise ActivityArtifactError("output path must end in .json or .geojson")
    part = output.with_name(output.name + LIVING_POPULATION_COMPACT_PART_SUFFIX)
    if output in resolved_inputs:
        raise ActivityArtifactError("input cannot be the output path")
    for path in (output, part):
        if path.exists():
            raise ActivityArtifactError(
                f"refusing to overwrite existing output or partial file: {path}"
            )
    return resolved_inputs, output, part


def _read_rows(
    inputs: list[Path], *, target_date: date, hour: int
) -> list[_SourceRow]:
    input_list = ", ".join(_sql_string(str(path)) for path in inputs)
    relation = f"read_parquet([{input_list}], union_by_name = false)"
    connection = duckdb.connect(":memory:")
    try:
        columns = {
            str(row[0])
            for row in connection.execute(
                f"DESCRIBE SELECT * FROM {relation}"
            ).fetchall()
        }
        missing = sorted(REQUIRED_PARQUET_COLUMNS - columns)
        if missing:
            raise ActivityArtifactError(
                "compact Parquet missing columns: " + ", ".join(missing)
            )
        raw_rows = connection.execute(
            f"""
            SELECT date, hour, cell_id, total, masked, source_file
            FROM {relation}
            WHERE hour = ? AND date <= ?
            ORDER BY date, cell_id, source_file
            """,
            [hour, target_date],
        ).fetchall()
    except duckdb.Error as exc:
        raise ActivityArtifactError(f"cannot read compact Parquet: {exc}") from exc
    finally:
        connection.close()

    rows: list[_SourceRow] = []
    seen: set[tuple[date, int, str]] = set()
    for raw_date, raw_hour, raw_cell, raw_total, raw_masked, raw_source in raw_rows:
        if not isinstance(raw_date, date):
            raise ActivityArtifactError("Parquet date must use DATE type")
        if not isinstance(raw_hour, int) or not 0 <= raw_hour <= 23:
            raise ActivityArtifactError("Parquet hour must be an integer in 0..23")
        if not isinstance(raw_cell, str) or not raw_cell.strip():
            raise ActivityArtifactError("Parquet cell_id must be non-empty")
        cell_id = raw_cell.strip()
        try:
            cell_wgs84_corners(cell_id)
        except ValueError as exc:
            raise ActivityArtifactError(
                f"invalid Parquet cell_id {cell_id!r}: {exc}"
            ) from None
        if not isinstance(raw_masked, bool):
            raise ActivityArtifactError("Parquet masked must use BOOLEAN type")
        if raw_masked:
            if raw_total is not None:
                raise ActivityArtifactError("masked Parquet row total must be NULL")
            total = None
        else:
            if raw_total is None:
                raise ActivityArtifactError(
                    "unmasked Parquet row total must not be NULL"
                )
            total = float(raw_total)
            if not 0 <= total < float("inf"):
                raise ActivityArtifactError(
                    "unmasked Parquet row total must be finite and non-negative"
                )
        if not isinstance(raw_source, str) or not raw_source.strip():
            raise ActivityArtifactError("Parquet source_file must be non-empty")
        identity = (raw_date, raw_hour, cell_id)
        if identity in seen:
            raise ActivityArtifactError(
                f"duplicate date-hour-cell observation: {identity!r}"
            )
        seen.add(identity)
        rows.append(
            _SourceRow(
                observed_date=raw_date,
                hour=raw_hour,
                cell_id=cell_id,
                total=total,
                masked=raw_masked,
                source_file=raw_source.strip(),
            )
        )
    return rows


def _serialize_baseline(estimate: TemporalBaselineEstimate) -> dict[str, Any]:
    return {
        "model_version": estimate.provenance.model_version,
        "mean": estimate.mean,
        "log_dispersion": estimate.dispersion,
        "raw_n": estimate.raw_n,
        "effective_n": estimate.effective_n,
        "masked_share": estimate.masked_share,
        "fallback_depth": estimate.fallback_depth,
        "selected_level": estimate.provenance.selected_level,
        "day_type": estimate.day_type,
        "iso_weekday": estimate.iso_weekday,
        "window_start_inclusive": estimate.window.start_inclusive.isoformat(),
        "window_end_exclusive": estimate.window.end_exclusive.isoformat(),
        "cutoff_policy": estimate.provenance.cutoff_policy,
    }


def _serialize_activity(estimate: ActivityShadowEstimate) -> dict[str, Any]:
    return {
        "model_version": estimate.model_version,
        "signal_mode": estimate.signal_mode,
        "freshness": estimate.freshness,
        "baseline_mean": estimate.baseline_mean,
        "current_value": estimate.current_value,
        "current_value_min": estimate.current_value_min,
        "current_value_max": estimate.current_value_max,
        "anomaly_log1p": estimate.anomaly_log1p,
        "anomaly_log1p_min": estimate.anomaly_log1p_min,
        "anomaly_log1p_max": estimate.anomaly_log1p_max,
        "standardized_anomaly": estimate.standardized_anomaly,
        "standardized_anomaly_min": estimate.standardized_anomaly_min,
        "standardized_anomaly_max": estimate.standardized_anomaly_max,
        "quality": estimate.quality,
        "calibrated_probability": estimate.calibrated_probability,
        "is_calibrated_probability": estimate.is_calibrated_probability,
    }


def _unsupported_activity() -> dict[str, Any]:
    return _serialize_activity(
        calculate_activity_shadow(
            OBSERVATION_TYPE,
            "unsupported",
            [],
            now=datetime(2000, 1, 1, tzinfo=ZoneInfo(SEOUL_TIMEZONE)),
        )
    )


def _ring(cell_id: str) -> list[list[float]]:
    corners = [[lng, lat] for lat, lng in cell_wgs84_corners(cell_id)]
    return [*corners, corners[0]]


def _baseline_reference(
    estimate: TemporalBaselineEstimate,
) -> ActivityBaselineReference:
    provenance = estimate.provenance
    if (
        provenance.selected_level is None
        or estimate.fallback_depth is None
        or estimate.masked_share is None
    ):
        raise ActivityArtifactError("available baseline lacks required provenance")
    return ActivityBaselineReference(
        model_version=provenance.model_version,
        source_version=provenance.source_version,
        source_hashes=provenance.source_hashes,
        calendar_version=provenance.calendar_version,
        window_start=estimate.window.start_inclusive,
        window_end_exclusive=estimate.window.end_exclusive,
        cutoff=estimate.window.end_exclusive,
        selected_bucket=provenance.selected_level,
        raw_n=estimate.raw_n,
        effective_n=estimate.effective_n,
        fallback_depth=estimate.fallback_depth,
        masked_share=estimate.masked_share,
    )


def _feature(
    *,
    cell_id: str,
    rows: list[_SourceRow],
    target_date: date,
    hour: int,
    calendar: CalendarSpec,
    source_version: str,
    source_hashes: tuple[str, ...],
) -> dict[str, Any]:
    target_rows = [row for row in rows if row.observed_date == target_date]
    if len(target_rows) > 1:
        raise ActivityArtifactError(f"duplicate target observation for {cell_id}")
    target = target_rows[0] if target_rows else None
    history = [row for row in rows if row.observed_date < target_date]
    historical_observations = [
        HistoricalCellObservation(
            cell_id=row.cell_id,
            observed_date=row.observed_date,
            hour=row.hour,
            total=row.total,
            masked=row.masked,
        )
        for row in history
    ]
    baseline = estimate_temporal_baseline_shadow(
        cell_id,
        target_date,
        hour,
        historical_observations,
        cutoff=target_date,
        public_holidays=calendar.public_holidays,
        calendar_version=calendar.version,
        source_version=source_version,
        source_hashes=source_hashes,
    )
    target_timestamp = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        tzinfo=ZoneInfo(calendar.timezone),
    )
    target_status = (
        "missing" if target is None else "masked" if target.masked else "observed"
    )
    if baseline.mean is None:
        activity = _unsupported_activity()
    else:
        mode = "observed" if target_status == "observed" else "baseline_only"
        current_value = target.total if target_status == "observed" else None
        contributor = ActivityContributorInput(
            contributor_id=cell_id,
            observation_type=OBSERVATION_TYPE,
            baseline_mean=baseline.mean,
            baseline_log_dispersion=baseline.dispersion,
            baseline_reference=_baseline_reference(baseline),
            value=current_value,
            value_min=None,
            value_max=None,
            observed_at=target_timestamp if current_value is not None else None,
            # Compact Parquet has no fetch timestamp.  The target timestamp is
            # an explicit deterministic evaluation reference, not a live fetch.
            fetched_at=target_timestamp,
            weight=1.0,
            freshness_score=1.0,
            quality=max(0.0, 1.0 - (baseline.masked_share or 0.0)),
            source_id=SOURCE_ID,
            source_version=source_version,
            geometry=CELL_GEOMETRY_VERSION,
            provenance=(
                "offline compact living-population parquet; "
                "evaluation-reference=target-timestamp; no fetch timestamp"
            ),
        )
        activity = _serialize_activity(
            calculate_activity_shadow(
                OBSERVATION_TYPE,
                mode,
                [contributor],
                now=target_timestamp,
            )
        )
    row_sources = sorted({row.source_file for row in rows})
    return {
        "type": "Feature",
        "id": cell_id,
        "geometry": {"type": "Polygon", "coordinates": [_ring(cell_id)]},
        "properties": {
            "cell_id": cell_id,
            "target_date": target_date.isoformat(),
            "hour": hour,
            "target_at": target_timestamp.isoformat(),
            "observed_at": (
                target_timestamp.isoformat() if target is not None else None
            ),
            "observation_type": OBSERVATION_TYPE,
            "target_status": target_status,
            "source_observation": {
                "total": target.total if target is not None else None,
                "masked": target.masked if target is not None else None,
                "source_file": target.source_file if target is not None else None,
            },
            "baseline": _serialize_baseline(baseline),
            "activity": activity,
            "source_id": SOURCE_ID,
            "source_version": source_version,
            "calendar_version": calendar.version,
            "provenance": {
                "artifact_model_version": ARTIFACT_MODEL_VERSION,
                "compact_schema_contract": {
                    "expected_version": LIVING_POPULATION_COMPACT_SCHEMA_VERSION,
                    "validation": "required-columns-and-row-contract;manifest-not-read",
                },
                "source_files": row_sources,
                "source_hashes": list(source_hashes),
                "calendar_sha256": calendar.sha256,
                "baseline_cutoff": "date<target_date",
                "masked_current_policy": "no-current;baseline-only-or-unsupported",
                "freshness_reference": "target-timestamp;offline-not-live-fetch",
                "geometry": f"{CELL_GEOMETRY_VERSION};cell_wgs84_corners;closed-ring",
            },
        },
    }


def build_activity_artifact(
    *,
    inputs: list[Path],
    calendar_path: Path,
    target_date: date,
    hour: int,
    source_version: str,
    output_path: Path,
    apply: bool = False,
) -> ActivityArtifactResult:
    """Build and optionally atomically publish one historical GeoJSON artifact."""

    if not isinstance(target_date, date):
        raise ActivityArtifactError("target_date must be a date")
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ActivityArtifactError("hour must be an integer in 0..23")
    if not source_version.strip():
        raise ActivityArtifactError("source_version must be non-empty")
    inputs, output, part = _preflight(inputs, output_path)
    calendar = _load_calendar(calendar_path)
    input_evidence = [
        {"file": path.name, "sha256": _sha256(path), "size_bytes": path.stat().st_size}
        for path in inputs
    ]
    source_hashes = tuple(
        f"sha256:{item['sha256']}" for item in input_evidence
    )
    rows = _read_rows(inputs, target_date=target_date, hour=hour)
    if not rows:
        raise ActivityArtifactError(
            "no rows at requested hour on or before target date"
        )
    grouped: dict[str, list[_SourceRow]] = defaultdict(list)
    for row in rows:
        grouped[row.cell_id].append(row)
    features = [
        _feature(
            cell_id=cell_id,
            rows=grouped[cell_id],
            target_date=target_date,
            hour=hour,
            calendar=calendar,
            source_version=source_version.strip(),
            source_hashes=source_hashes,
        )
        for cell_id in sorted(grouped)
    ]
    counts = {
        "features": len(features),
        "observed": sum(
            feature["properties"]["activity"]["signal_mode"] == "observed"
            for feature in features
        ),
        "baseline_only": sum(
            feature["properties"]["activity"]["signal_mode"] == "baseline_only"
            for feature in features
        ),
        "unsupported": sum(
            feature["properties"]["activity"]["signal_mode"] == "unsupported"
            for feature in features
        ),
        "masked_target": sum(
            feature["properties"]["target_status"] == "masked"
            for feature in features
        ),
        "missing_target": sum(
            feature["properties"]["target_status"] == "missing"
            for feature in features
        ),
    }
    artifact: dict[str, Any] = {
        "type": "FeatureCollection",
        "model": {
            "artifact": ARTIFACT_MODEL_VERSION,
            "observation_type": OBSERVATION_TYPE,
        },
        "target": {
            "date": target_date.isoformat(),
            "hour": hour,
            "timezone": calendar.timezone,
        },
        "source": {
            "id": SOURCE_ID,
            "version": source_version.strip(),
            "compact_schema_contract": {
                "expected_version": LIVING_POPULATION_COMPACT_SCHEMA_VERSION,
                "validation": "required-columns-and-row-contract;manifest-not-read",
            },
            "inputs": input_evidence,
        },
        "calendar": {
            "version": calendar.version,
            "timezone": calendar.timezone,
            "file": calendar.source_file,
            "sha256": calendar.sha256,
            "public_holiday_count": len(calendar.public_holidays),
        },
        "provenance": {
            "baseline_cutoff": "strictly-before-target-date",
            "masked_current_policy": "no-current;never-point-imputed",
            "geometry": f"{CELL_GEOMETRY_VERSION};decoder-quadrilateral-wgs84",
            "ordering": "features=cell_id;json-keys=lexicographic",
            "network_calls": False,
        },
        "counts": counts,
        "features": features,
    }
    serialized = (
        json.dumps(
            artifact,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
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
    return ActivityArtifactResult(
        artifact=artifact,
        output_path=output,
        serialized=serialized,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--hour", required=True, type=int)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="publish output (default: dry-run)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_activity_artifact(
            inputs=args.input,
            calendar_path=args.calendar,
            target_date=_parse_iso_date(args.target_date, field="target_date"),
            hour=args.hour,
            source_version=args.source_version,
            output_path=args.output,
            apply=args.apply,
        )
        print(result.serialized.decode("utf-8"), end="")
        if not args.apply:
            print("dry-run: pass --apply to publish", file=sys.stderr)
        return 0
    except (ActivityArtifactError, OSError) as exc:
        print(f"activity artifact failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
