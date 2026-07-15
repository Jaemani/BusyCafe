#!/usr/bin/env python3
"""Build versioned centroids for the Seoul purpose-OD shadow experiment.

The OA-22300 file uses eight-digit Seoul administrative-dong codes and
eight-digit non-Seoul si/gun/gu codes padded with ``000``.  This offline tool
derives both from one date-matched national administrative-boundary snapshot.
It never calls a provider and is a no-write dry-run unless ``--apply`` is set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (  # noqa: E402
    PURPOSE_OD_CENTROID_SCHEMA_VERSION,
    PURPOSE_OD_HASH_CHUNK_BYTES,
)


SOURCE_CRS = "EPSG:4326"
METRIC_CRS = "EPSG:5179"
SEOUL_SIDO_CODE = "11"


class PurposeOdCentroidError(ValueError):
    """Raised when boundary input cannot produce a trustworthy artifact."""


@dataclass(frozen=True, slots=True)
class PurposeOdCentroidResult:
    artifact: dict[str, Any]
    serialized: bytes
    output_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(PURPOSE_OD_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _nonempty_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PurposeOdCentroidError(f"{field} must be a non-empty string")
    return value.strip()


def _digits(value: object, *, field: str, length: int) -> str:
    text = _nonempty_text(value, field=field)
    if len(text) != length or not text.isascii() or not text.isdigit():
        raise PurposeOdCentroidError(
            f"{field} must be exactly {length} ASCII digits: {text!r}"
        )
    return text


def _load_features(path: Path) -> list[dict[str, Any]]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise PurposeOdCentroidError(f"boundary file does not exist: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PurposeOdCentroidError(f"cannot read boundary GeoJSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise PurposeOdCentroidError("boundary root must be a GeoJSON FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise PurposeOdCentroidError("boundary FeatureCollection must not be empty")
    if any(not isinstance(item, dict) for item in features):
        raise PurposeOdCentroidError("every boundary feature must be an object")
    return features


def _centroid_record(
    *,
    code: str,
    name: str,
    kind: str,
    geometries: Iterable[Any],
    to_metric: Transformer,
    to_wgs84: Transformer,
) -> dict[str, object]:
    projected = [transform(to_metric.transform, geometry) for geometry in geometries]
    if not projected:
        raise PurposeOdCentroidError(f"no geometry for {code}")
    merged = unary_union(projected)
    if merged.is_empty or not merged.is_valid:
        raise PurposeOdCentroidError(f"invalid dissolved geometry for {code}")
    center = transform(to_wgs84.transform, merged.centroid)
    return {
        "code": code,
        "kind": kind,
        "name": name,
        "lat": round(float(center.y), 8),
        "lng": round(float(center.x), 8),
    }


def build_purpose_od_centroids(
    *,
    source_geojson: Path,
    source_version: str,
    source_commit: str,
    output_path: Path,
    apply: bool = False,
) -> PurposeOdCentroidResult:
    """Derive deterministic Seoul-dong and national si/gun/gu centroids."""

    source = source_geojson.resolve()
    output = output_path.resolve()
    part = output.with_name(output.name + ".part")
    version = _nonempty_text(source_version, field="source_version")
    commit = _nonempty_text(source_commit, field="source_commit")
    if output.suffix.lower() != ".json":
        raise PurposeOdCentroidError("output path must end in .json")
    if output == source:
        raise PurposeOdCentroidError("source and output paths must differ")
    for candidate in (output, part):
        if candidate.exists():
            raise PurposeOdCentroidError(
                f"refusing to overwrite output or partial file: {candidate}"
            )

    features = _load_features(source)
    seoul_dongs: dict[str, tuple[str, Any]] = {}
    sgg_geometries: dict[str, list[Any]] = defaultdict(list)
    sgg_names: dict[str, str] = {}
    for index, feature in enumerate(features):
        properties = feature.get("properties")
        geometry_payload = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry_payload, dict):
            raise PurposeOdCentroidError(
                f"feature {index} must contain properties and geometry objects"
            )
        adm_cd2 = _digits(properties.get("adm_cd2"), field="adm_cd2", length=10)
        sido = _digits(properties.get("sido"), field="sido", length=2)
        sgg = _digits(properties.get("sgg"), field="sgg", length=5)
        adm_name = _nonempty_text(properties.get("adm_nm"), field="adm_nm")
        sido_name = _nonempty_text(properties.get("sidonm"), field="sidonm")
        sgg_name = _nonempty_text(properties.get("sggnm"), field="sggnm")
        try:
            geometry = shape(geometry_payload)
        except Exception as exc:  # Shapely exposes several format exceptions.
            raise PurposeOdCentroidError(
                f"feature {index} has invalid geometry: {exc}"
            ) from exc
        if geometry.is_empty or not geometry.is_valid:
            raise PurposeOdCentroidError(f"feature {index} has empty/invalid geometry")

        sgg_code = sgg + "000"
        sgg_geometries[sgg_code].append(geometry)
        expected_sgg_name = f"{sido_name} {sgg_name}"
        prior_name = sgg_names.setdefault(sgg_code, expected_sgg_name)
        if prior_name != expected_sgg_name:
            raise PurposeOdCentroidError(f"conflicting names for {sgg_code}")

        if sido == SEOUL_SIDO_CODE:
            dong_code = adm_cd2[:8]
            if dong_code in seoul_dongs:
                raise PurposeOdCentroidError(f"duplicate Seoul dong code {dong_code}")
            seoul_dongs[dong_code] = (adm_name, geometry)

    to_metric = Transformer.from_crs(SOURCE_CRS, METRIC_CRS, always_xy=True)
    to_wgs84 = Transformer.from_crs(METRIC_CRS, SOURCE_CRS, always_xy=True)
    centroids = [
        _centroid_record(
            code=code,
            name=name,
            kind="seoul_admin_dong",
            geometries=[geometry],
            to_metric=to_metric,
            to_wgs84=to_wgs84,
        )
        for code, (name, geometry) in sorted(seoul_dongs.items())
    ]
    centroids.extend(
        _centroid_record(
            code=code,
            name=sgg_names[code],
            kind="si_gun_gu",
            geometries=sgg_geometries[code],
            to_metric=to_metric,
            to_wgs84=to_wgs84,
        )
        for code in sorted(sgg_geometries)
    )
    centroids.sort(key=lambda item: str(item["code"]))
    codes = [str(item["code"]) for item in centroids]
    if len(codes) != len(set(codes)):
        overlap = sorted(code for code in set(codes) if codes.count(code) > 1)
        raise PurposeOdCentroidError(f"duplicate output codes: {overlap[:5]}")

    artifact: dict[str, Any] = {
        "schema_version": PURPOSE_OD_CENTROID_SCHEMA_VERSION,
        "crs": SOURCE_CRS,
        "source": {
            "name": "vuski/admdongkor",
            "version": version,
            "commit": commit,
            "filename": source.name,
            "sha256": _sha256(source),
            "source_crs": SOURCE_CRS,
            "centroid_crs": METRIC_CRS,
        },
        "counts": {
            "seoul_admin_dong": len(seoul_dongs),
            "si_gun_gu": len(sgg_geometries),
            "total": len(centroids),
        },
        "centroids": centroids,
    }
    serialized = (
        json.dumps(
            artifact,
            ensure_ascii=False,
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
    return PurposeOdCentroidResult(
        artifact=artifact,
        serialized=serialized,
        output_path=output,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-geojson", required=True, type=Path)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="atomically publish the artifact"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_purpose_od_centroids(
            source_geojson=args.source_geojson,
            source_version=args.source_version,
            source_commit=args.source_commit,
            output_path=args.output,
            apply=args.apply,
        )
    except PurposeOdCentroidError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "output": str(result.output_path),
                "sha256": hashlib.sha256(result.serialized).hexdigest(),
                "counts": result.artifact["counts"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
