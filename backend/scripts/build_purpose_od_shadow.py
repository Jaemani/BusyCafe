#!/usr/bin/env python3
"""Build one deterministic, offline purpose-OD movement shadow artifact.

The source is one verified Seoul purpose-OD CSV or single-member ZIP.  The
artifact groups arrivals by destination/finish hour and departures by
origin/start hour.  Optional, explicitly versioned WGS84
administrative-dong centroids enable a coarse origin-to-destination direction
estimate.  Missing geometry stays visible as coverage loss; it is never
geocoded or inferred here.

No network, database, or public API is used.  Dry-run is the default and
``--apply`` atomically publishes a new JSON file without overwriting anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ingest.purpose_od import (  # noqa: E402
    PurposeOdCsvError,
    PurposeOdRecord,
    iter_purpose_od_csv,
    iter_purpose_od_zip,
)
from app.config import (  # noqa: E402
    PURPOSE_OD_HASH_CHUNK_BYTES,
    PURPOSE_OD_SHADOW_MODEL_VERSION,
)
from app.scoring.od_flow_shadow import (  # noqa: E402
    ODFlowObservation,
    ODZoneCentroid,
    aggregate_od_flow_shadow,
)


SOURCE_ID = "seoul-purpose-od"
CENTROID_CRS = "EPSG:4326"
PART_SUFFIX = ".part"


class PurposeOdArtifactError(ValueError):
    """Raised when an artifact cannot be built without guessing."""


@dataclass(frozen=True, slots=True)
class AdministrativeDongCentroid:
    code: str
    name: str | None
    kind: str | None
    lat: float
    lng: float


@dataclass(frozen=True, slots=True)
class CentroidCatalog:
    schema_version: str
    crs: str
    sha256: str
    source_file: str
    source: dict[str, Any]
    centroids: dict[str, AdministrativeDongCentroid]


@dataclass(frozen=True, slots=True)
class PurposeOdArtifactResult:
    artifact: dict[str, Any]
    output_path: Path
    serialized: bytes


@dataclass(slots=True)
class _ArrivalEvidence:
    inbound_count: Decimal = Decimal(0)
    weighted_distance_m: Decimal = Decimal(0)
    weighted_duration_min: Decimal = Decimal(0)
    total_rows: int = 0
    complete_centroid_rows: int = 0
    complete_centroid_count: Decimal = Decimal(0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(PURPOSE_OD_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_iso_date(raw: object, *, field_name: str) -> date:
    if not isinstance(raw, str):
        raise PurposeOdArtifactError(f"{field_name} must be an ISO date string")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise PurposeOdArtifactError(
            f"invalid ISO date in {field_name}: {raw!r}"
        ) from None
    if parsed.isoformat() != raw:
        raise PurposeOdArtifactError(
            f"non-canonical ISO date in {field_name}: {raw!r}"
        )
    return parsed


def _validate_code(raw: object, *, field_name: str) -> str:
    if (
        not isinstance(raw, str)
        or len(raw) != 8
        or not raw.isascii()
        or not raw.isdigit()
    ):
        raise PurposeOdArtifactError(
            f"{field_name} must be eight ASCII digits"
        )
    return raw


def _finite_coordinate(raw: object, *, field_name: str, low: float, high: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PurposeOdArtifactError(f"{field_name} must be numeric")
    value = float(raw)
    if not math.isfinite(value) or not low <= value <= high:
        raise PurposeOdArtifactError(
            f"{field_name} must be finite and within [{low}, {high}]"
        )
    return value


def _load_centroids(path: Path | None) -> CentroidCatalog | None:
    if path is None:
        return None
    resolved = path.resolve()
    if not resolved.is_file():
        raise PurposeOdArtifactError(f"centroid catalog does not exist: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PurposeOdArtifactError(
            f"cannot read centroid catalog {resolved}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PurposeOdArtifactError("centroid catalog must be a JSON object")
    schema_version = payload.get("schema_version", payload.get("version"))
    source_provenance = payload.get("source")
    # The centroid builder publishes WGS84 coordinates while preserving its
    # metric centroid CRS inside source provenance.
    crs = payload.get("crs", CENTROID_CRS)
    entries = payload.get("centroids")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise PurposeOdArtifactError("centroid schema_version must be non-empty")
    if not isinstance(source_provenance, dict):
        raise PurposeOdArtifactError("centroid source must be an object")
    source_version = source_provenance.get("version")
    if not isinstance(source_version, str) or not source_version.strip():
        raise PurposeOdArtifactError("centroid source.version must be non-empty")
    if crs != CENTROID_CRS:
        raise PurposeOdArtifactError(
            f"centroid crs must be explicit {CENTROID_CRS!r}"
        )
    if not isinstance(entries, list) or not entries:
        raise PurposeOdArtifactError("centroids must be a non-empty list")

    centroids: dict[str, AdministrativeDongCentroid] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PurposeOdArtifactError(f"centroids[{index}] must be an object")
        code = _validate_code(entry.get("code"), field_name=f"centroids[{index}].code")
        if code in centroids:
            raise PurposeOdArtifactError(f"duplicate centroid code: {code}")
        name_raw = entry.get("name")
        kind_raw = entry.get("kind")
        for raw, field_name in (
            (name_raw, f"centroids[{index}].name"),
            (kind_raw, f"centroids[{index}].kind"),
        ):
            if raw is not None and (
                not isinstance(raw, str) or not raw.strip() or raw != raw.strip()
            ):
                raise PurposeOdArtifactError(
                    f"{field_name} must be canonical non-empty text when present"
                )
        lat = _finite_coordinate(
            entry.get("lat"),
            field_name=f"centroids[{index}].lat",
            low=-90.0,
            high=90.0,
        )
        lng = _finite_coordinate(
            entry.get("lng"),
            field_name=f"centroids[{index}].lng",
            low=-180.0,
            high=180.0,
        )
        centroids[code] = AdministrativeDongCentroid(
            code=code,
            name=name_raw,
            kind=kind_raw,
            lat=lat,
            lng=lng,
        )
    return CentroidCatalog(
        schema_version=schema_version.strip(),
        crs=crs,
        sha256=_sha256(resolved),
        source_file=resolved.name,
        source=source_provenance,
        centroids=centroids,
    )


def _records(path: Path) -> Iterable[PurposeOdRecord]:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return iter_purpose_od_zip(path)
    if suffix == ".csv":
        return iter_purpose_od_csv(path)
    raise PurposeOdArtifactError("input must be a .csv or .zip file")


def _decimal_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    return float(value)


def _ratio(numerator: Decimal, denominator: Decimal) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator / denominator), 9)


def _clock(minute: int) -> str:
    if not isinstance(minute, int) or not 0 <= minute < 24 * 60:
        raise PurposeOdArtifactError(f"time minute outside one day: {minute!r}")
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _preflight(
    input_path: Path,
    centroid_path: Path | None,
    output_path: Path,
) -> tuple[Path, Path | None, Path, Path]:
    source = input_path.resolve()
    if not source.is_file():
        raise PurposeOdArtifactError(f"input does not exist: {source}")
    centroids = centroid_path.resolve() if centroid_path is not None else None
    output = output_path.resolve()
    if output.suffix.lower() != ".json":
        raise PurposeOdArtifactError("output path must end in .json")
    if source == output or centroids == output:
        raise PurposeOdArtifactError("inputs cannot also be the output")
    part = output.with_name(output.name + PART_SUFFIX)
    for candidate in (output, part):
        if candidate.exists():
            raise PurposeOdArtifactError(
                f"refusing to overwrite existing output or partial file: {candidate}"
            )
    return source, centroids, output, part


def build_purpose_od_shadow(
    *,
    input_path: Path,
    centroid_path: Path | None,
    target_date: date,
    source_version: str,
    schema_version: str,
    output_path: Path,
    hours: frozenset[int] | None = None,
    apply: bool = False,
) -> PurposeOdArtifactResult:
    """Build and optionally publish one historical purpose-OD JSON artifact."""

    if not isinstance(target_date, date):
        raise PurposeOdArtifactError("target_date must be a date")
    if not source_version.strip():
        raise PurposeOdArtifactError("source_version must be non-empty")
    if not schema_version.strip():
        raise PurposeOdArtifactError("schema_version must be non-empty")
    if hours is not None:
        if not hours:
            raise PurposeOdArtifactError("hours must not be empty when supplied")
        if any(
            not isinstance(hour, int)
            or isinstance(hour, bool)
            or not 0 <= hour <= 23
            for hour in hours
        ):
            raise PurposeOdArtifactError("hours must contain integers in 0..23")
    source, centroid_source, output, part = _preflight(
        input_path, centroid_path, output_path
    )
    centroid_catalog = _load_centroids(centroid_source)
    centroid_map = centroid_catalog.centroids if centroid_catalog is not None else {}

    evidence: dict[tuple[str, int], _ArrivalEvidence] = defaultdict(_ArrivalEvidence)
    total_count = Decimal(0)
    complete_centroid_count = Decimal(0)
    source_rows_scanned = 0
    selected_rows = 0
    complete_centroid_rows = 0
    intrazonal_rows = 0
    intrazonal_count = Decimal(0)
    observed_zone_codes: set[str] = set()
    missing_origin_codes: set[str] = set()
    missing_destination_codes: set[str] = set()
    source_start_time_bins: set[int] = set()
    source_finish_time_bins: set[int] = set()

    engine_centroids = [
        ODZoneCentroid(zone_id=item.code, lat=item.lat, lng=item.lng)
        for item in sorted(centroid_map.values(), key=lambda item: item.code)
    ]

    def selected_observations() -> Iterable[ODFlowObservation]:
        nonlocal complete_centroid_count
        nonlocal complete_centroid_rows
        nonlocal intrazonal_count
        nonlocal intrazonal_rows
        nonlocal selected_rows
        nonlocal source_rows_scanned
        nonlocal total_count

        for record in _records(source):
            source_rows_scanned += 1
            if record.observed_date != target_date:
                raise PurposeOdArtifactError(
                    "input contains a row outside target_date: "
                    f"{record.observed_date.isoformat()} != {target_date.isoformat()}"
                )
            source_start_time_bins.add(record.start_minute)
            source_finish_time_bins.add(record.finish_minute)
            departure_hour = record.departure_hour
            arrival_hour = record.arrival_hour
            if hours is not None and not (
                departure_hour in hours or arrival_hour in hours
            ):
                continue
            count = record.estimated_count
            selected_rows += 1
            total_count += count
            origin_code = record.origin_administrative_dong_code
            destination_code = record.destination_administrative_dong_code
            observed_zone_codes.update((origin_code, destination_code))
            accumulator = evidence[(destination_code, arrival_hour)]
            accumulator.total_rows += 1
            accumulator.inbound_count += count
            accumulator.weighted_distance_m += count * Decimal(record.distance_m)
            accumulator.weighted_duration_min += count * Decimal(record.duration_min)

            origin = centroid_map.get(origin_code)
            destination = centroid_map.get(destination_code)
            if origin is None:
                missing_origin_codes.add(origin_code)
            if destination is None:
                missing_destination_codes.add(destination_code)
            if origin is not None and destination is not None:
                complete_centroid_rows += 1
                complete_centroid_count += count
                accumulator.complete_centroid_rows += 1
                accumulator.complete_centroid_count += count
            if origin_code == destination_code:
                intrazonal_rows += 1
                intrazonal_count += count

            yield ODFlowObservation(
                origin_id=origin_code,
                destination_id=destination_code,
                departure_hour=departure_hour,
                arrival_hour=arrival_hour,
                purpose=str(record.purpose),
                flow=count,
            )

    aggregates = aggregate_od_flow_shadow(selected_observations(), engine_centroids)
    if source_rows_scanned == 0:
        raise PurposeOdArtifactError("input contains no purpose-OD rows")
    if selected_rows == 0:
        raise PurposeOdArtifactError("input has no rows affecting selected hours")

    movements: list[dict[str, Any]] = []
    for aggregate in aggregates:
        if hours is not None and aggregate.hour not in hours:
            continue
        accumulator = evidence.get((aggregate.zone_id, aggregate.hour))
        centroid = centroid_map.get(aggregate.zone_id)
        inbound_decimal = (
            accumulator.inbound_count if accumulator is not None else Decimal(0)
        )
        if aggregate.direction_bearing_deg is None:
            origin_bearing = None
        else:
            origin_bearing = round(
                (aggregate.direction_bearing_deg + 180.0) % 360.0, 6
            )
        movements.append(
            {
                "administrative_zone_code": aggregate.zone_id,
                "zone_name": centroid.name if centroid is not None else None,
                "zone_kind": centroid.kind if centroid is not None else None,
                "hour": aggregate.hour,
                "local_time": _clock(aggregate.hour * 60),
                "inbound_estimated_count": round(aggregate.inbound, 6),
                "outbound_estimated_count": round(aggregate.outbound, 6),
                "net_estimated_count": round(aggregate.net, 6),
                "intrazonal_inbound_estimated_count": round(
                    aggregate.intrazonal_inbound, 6
                ),
                "intrazonal_outbound_estimated_count": round(
                    aggregate.intrazonal_outbound, 6
                ),
                "purpose_estimated_counts": {
                    item.purpose: round(item.total, 6)
                    for item in aggregate.purposes
                },
                "purpose_ratios": {
                    item.purpose: round(item.ratio, 9)
                    for item in aggregate.purposes
                },
                "mean_source_distance_m": (
                    round(
                        float(accumulator.weighted_distance_m / inbound_decimal),
                        6,
                    )
                    if accumulator is not None and inbound_decimal > 0
                    else None
                ),
                "mean_source_duration_min": (
                    round(
                        float(accumulator.weighted_duration_min / inbound_decimal),
                        6,
                    )
                    if accumulator is not None and inbound_decimal > 0
                    else None
                ),
                "zone_centroid": (
                    {"lat": centroid.lat, "lng": centroid.lng}
                    if centroid is not None
                    else None
                ),
                "geometry_coverage": {
                    "rows": accumulator.complete_centroid_rows if accumulator else 0,
                    "total_rows": accumulator.total_rows if accumulator else 0,
                    "estimated_count": _decimal_number(
                        accumulator.complete_centroid_count
                        if accumulator
                        else Decimal(0)
                    ),
                    "estimated_count_ratio": _ratio(
                        accumulator.complete_centroid_count
                        if accumulator
                        else Decimal(0),
                        inbound_decimal,
                    ),
                },
                "movement_vector": {
                    "east_component": (
                        round(aggregate.direction_east, 9)
                        if aggregate.direction_east is not None
                        else None
                    ),
                    "north_component": (
                        round(aggregate.direction_north, 9)
                        if aggregate.direction_north is not None
                        else None
                    ),
                    "travel_heading_deg": (
                        round(aggregate.direction_bearing_deg, 6)
                        if aggregate.direction_bearing_deg is not None
                        else None
                    ),
                    "origin_bearing_deg": origin_bearing,
                    "direction_strength": (
                        round(aggregate.direction_strength, 9)
                        if aggregate.direction_strength is not None
                        else None
                    ),
                    "eligible_estimated_count_coverage": round(
                        aggregate.direction_coverage, 9
                    ),
                },
            }
        )

    source_sha = _sha256(source)
    artifact: dict[str, Any] = {
        "artifact": {
            "model_version": PURPOSE_OD_SHADOW_MODEL_VERSION,
            "public_model_effect": "none; offline shadow only",
        },
        "target": {
            "date": target_date.isoformat(),
            "timezone": "Asia/Seoul",
            "hours": sorted(hours) if hours is not None else list(range(24)),
        },
        "source": {
            "id": SOURCE_ID,
            "version": source_version.strip(),
            "schema_version": schema_version.strip(),
            "file": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": source_sha,
        },
        "centroids": {
            "available": centroid_catalog is not None,
            "schema_version": (
                centroid_catalog.schema_version if centroid_catalog else None
            ),
            "crs": centroid_catalog.crs if centroid_catalog else None,
            "file": centroid_catalog.source_file if centroid_catalog else None,
            "sha256": centroid_catalog.sha256 if centroid_catalog else None,
            "source": centroid_catalog.source if centroid_catalog else None,
            "records": len(centroid_map),
        },
        "coverage": {
            "source_rows_scanned": source_rows_scanned,
            "selected_rows": selected_rows,
            "selected_estimated_count": _decimal_number(total_count),
            "observed_zone_codes": len(observed_zone_codes),
            "matched_centroid_zone_codes": len(
                observed_zone_codes.intersection(centroid_map)
            ),
            "centroid_code_ratio": round(
                len(observed_zone_codes.intersection(centroid_map))
                / len(observed_zone_codes),
                9,
            ),
            "complete_centroid_rows": complete_centroid_rows,
            "complete_centroid_estimated_count": _decimal_number(
                complete_centroid_count
            ),
            "complete_centroid_row_ratio": round(
                complete_centroid_rows / selected_rows, 9
            ),
            "complete_centroid_estimated_count_ratio": _ratio(
                complete_centroid_count, total_count
            ),
            "intrazonal_rows": intrazonal_rows,
            "intrazonal_estimated_count": _decimal_number(intrazonal_count),
            "missing_origin_codes": sorted(missing_origin_codes),
            "missing_destination_codes": sorted(missing_destination_codes),
        },
        "time_contract": {
            "normalization": "floor-to-hour; source mixed 60/20-minute bins",
            "distinct_source_start_bins": len(source_start_time_bins),
            "distinct_source_finish_bins": len(source_finish_time_bins),
        },
        "provenance": {
            "network_calls": False,
            "database_reads": False,
            "database_writes": False,
            "arrival_grouping": "destination_administrative_dong_code+finish_hour",
            "departure_grouping": "origin_administrative_dong_code+start_hour",
            "direction": (
                "estimated_count-weighted mean of origin-centroid-to-"
                "destination-centroid unit vectors; not an observed route"
            ),
            "origin_bearing": "opposite of travel_heading_deg",
            "direction_strength": "resultant unit-vector length; 0=cancelled, 1=aligned",
            "missing_geometry": "excluded from vectors and reported as coverage loss",
            "intrazonal_geometry": "included in totals; excluded from direction",
            "ordering": "movements=zone-code,hour;json-keys=lexicographic",
        },
        "movements": movements,
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
    return PurposeOdArtifactResult(
        artifact=artifact,
        output_path=output,
        serialized=serialized,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--centroids", type=Path)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--schema-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--hour",
        action="append",
        type=int,
        help=(
            "limit output to one hour (repeatable); rows touching the hour as "
            "departures or arrivals are retained"
        ),
    )
    parser.add_argument(
        "--apply", action="store_true", help="publish output (default: dry-run)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_purpose_od_shadow(
            input_path=args.input,
            centroid_path=args.centroids,
            target_date=_parse_iso_date(args.target_date, field_name="target_date"),
            source_version=args.source_version,
            schema_version=args.schema_version,
            output_path=args.output,
            hours=frozenset(args.hour) if args.hour is not None else None,
            apply=args.apply,
        )
        coverage = result.artifact["coverage"]
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    "output": str(result.output_path),
                    "artifact_sha256": hashlib.sha256(result.serialized).hexdigest(),
                    "movement_groups": len(result.artifact["movements"]),
                    "source_rows_scanned": coverage["source_rows_scanned"],
                    "selected_rows": coverage["selected_rows"],
                    "centroid_code_ratio": coverage["centroid_code_ratio"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if not args.apply:
            print("dry-run: pass --apply to publish", file=sys.stderr)
        return 0
    except (OSError, PurposeOdCsvError, ValueError) as exc:
        print(f"purpose OD artifact failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
