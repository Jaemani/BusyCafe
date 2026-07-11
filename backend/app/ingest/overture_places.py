"""Operator-run Overture Places cache ingestion utilities.

This module is intentionally outside the request path.  It reads a bounded
Seoul extract, validates records and materializes them into our database so
map viewport requests are local indexed queries only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    OVERTURE_CAFE_CATEGORIES,
    OVERTURE_MIN_CONFIDENCE,
    OVERTURE_S3_URI_TEMPLATE,
    SEOUL_BBOX,
)
from app.models import Cafe


class OvertureIngestError(ValueError):
    """A bounded Overture extract cannot safely be materialized."""


@dataclass(frozen=True, slots=True)
class OvertureCafeRecord:
    overture_id: str
    name: str
    lat: float
    lng: float
    primary_category: str
    confidence: float
    road_address: str | None
    phone: str | None
    website: str | None
    sources: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OvertureSeedReport:
    source_count: int
    inserted_count: int
    updated_count: int
    unchanged_count: int
    deactivated_count: int
    active_count: int
    dry_run: bool


def _optional_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def parse_overture_row(row: dict[str, object]) -> OvertureCafeRecord:
    """Parse a selected DuckDB row; used by both runtime and fixture tests."""

    overture_id = _optional_text(row.get("overture_id"), limit=64)
    name = _optional_text(row.get("name"), limit=255)
    category = _optional_text(row.get("primary_category"), limit=100)
    try:
        lat = float(row["lat"])
        lng = float(row["lng"])
        confidence = float(row["confidence"])
    except (KeyError, TypeError, ValueError) as error:
        raise OvertureIngestError("row has invalid coordinate or confidence") from error
    if not overture_id or not name or not category:
        raise OvertureIngestError("row is missing overture_id, name, or category")
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise OvertureIngestError("row coordinate is outside WGS84 range")
    if not 0 <= confidence <= 1:
        raise OvertureIngestError("row confidence must be between zero and one")
    raw_sources = row.get("sources_json")
    try:
        sources = json.loads(raw_sources) if isinstance(raw_sources, str) else raw_sources
    except json.JSONDecodeError as error:
        raise OvertureIngestError("row sources_json is invalid JSON") from error
    if sources is None:
        sources = []
    if not isinstance(sources, list) or not all(isinstance(item, dict) for item in sources):
        raise OvertureIngestError("row sources_json must be a list of objects")
    return OvertureCafeRecord(
        overture_id=overture_id,
        name=name,
        lat=lat,
        lng=lng,
        primary_category=category,
        confidence=confidence,
        road_address=_optional_text(row.get("road_address"), limit=500),
        phone=_optional_text(row.get("phone"), limit=64),
        website=_optional_text(row.get("website"), limit=500),
        sources=sources,
    )


def _duckdb_connection(*, require_httpfs: bool = False) -> Any:
    try:
        import duckdb
    except ImportError as error:  # pragma: no cover - packaging guard
        raise RuntimeError("duckdb dependency is required for Overture ingest") from error
    connection = duckdb.connect()
    if require_httpfs:
        connection.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    return connection


def cache_seoul_extract(
    output_path: Path,
    *,
    release: str,
    min_confidence: float = OVERTURE_MIN_CONFIDENCE,
    bbox: tuple[float, float, float, float] = SEOUL_BBOX,
    categories: Sequence[str] = OVERTURE_CAFE_CATEGORIES,
) -> int:
    """Fetch a bounded Overture subset to an operator-controlled local cache."""

    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing extract: {output_path}")
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between zero and one")
    if not categories:
        raise ValueError("at least one Overture category is required")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source = OVERTURE_S3_URI_TEMPLATE.format(release=release)
    category_values = ", ".join(f"'{category}'" for category in categories)
    min_lng, min_lat, max_lng, max_lat = bbox
    query = f"""
        COPY (
          SELECT
            id AS overture_id,
            names.primary AS name,
            bbox.ymin AS lat,
            bbox.xmin AS lng,
            categories.primary AS primary_category,
            confidence,
            addresses[1].freeform AS road_address,
            phones[1] AS phone,
            websites[1] AS website,
            to_json(sources) AS sources_json
          FROM read_parquet('{source}', hive_partitioning=1)
          WHERE bbox.xmin >= {min_lng} AND bbox.xmax <= {max_lng}
            AND bbox.ymin >= {min_lat} AND bbox.ymax <= {max_lat}
            AND categories.primary IN ({category_values})
            AND confidence >= {min_confidence}
        ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    connection = _duckdb_connection(require_httpfs=True)
    try:
        connection.execute(query)
        return int(
            connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(output_path)]
            ).fetchone()[0]
        )
    finally:
        connection.close()


def iter_cached_records(path: Path) -> Iterator[OvertureCafeRecord]:
    """Yield validated records from the immutable local cache extract."""

    if not path.is_file():
        raise FileNotFoundError(path)
    connection = _duckdb_connection()
    try:
        cursor = connection.execute("SELECT * FROM read_parquet(?)", [str(path)])
        columns = [description[0] for description in cursor.description]
        while rows := cursor.fetchmany(1_000):
            for values in rows:
                yield parse_overture_row(dict(zip(columns, values, strict=True)))
    finally:
        connection.close()


def _values(record: OvertureCafeRecord, *, release: str) -> dict[str, object]:
    return {
        "source_release": release,
        "source_confidence": record.confidence,
        "primary_category": record.primary_category,
        "name": record.name,
        "lat": record.lat,
        "lng": record.lng,
        "road_address": record.road_address,
        "phone": record.phone,
        "website": record.website,
        "source_json": record.sources,
        "active": True,
    }


def seed_overture_cafes(
    session: Session,
    records: Iterable[OvertureCafeRecord],
    *,
    release: str,
    dry_run: bool = False,
) -> OvertureSeedReport:
    """Idempotently materialize one release and deactivate missing records."""

    records_by_id: dict[str, OvertureCafeRecord] = {}
    for record in records:
        if record.overture_id in records_by_id:
            raise OvertureIngestError(f"duplicate Overture ID: {record.overture_id}")
        records_by_id[record.overture_id] = record
    if not records_by_id:
        raise OvertureIngestError("refusing to replace the POI cache with an empty extract")

    existing_by_id = {
        cafe.overture_id: cafe for cafe in session.scalars(select(Cafe))
    }
    inserted_count = updated_count = unchanged_count = deactivated_count = 0
    for overture_id in sorted(records_by_id):
        record = records_by_id[overture_id]
        values = _values(record, release=release)
        existing = existing_by_id.get(overture_id)
        if existing is None:
            inserted_count += 1
            if not dry_run:
                session.add(Cafe(overture_id=overture_id, **values))
            continue
        if all(getattr(existing, key) == value for key, value in values.items()):
            unchanged_count += 1
            continue
        updated_count += 1
        if not dry_run:
            for key, value in values.items():
                setattr(existing, key, value)

    for overture_id, existing in existing_by_id.items():
        if overture_id not in records_by_id and existing.active:
            deactivated_count += 1
            if not dry_run:
                existing.active = False
    if not dry_run:
        session.commit()

    return OvertureSeedReport(
        source_count=len(records_by_id),
        inserted_count=inserted_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        deactivated_count=deactivated_count,
        active_count=len(records_by_id),
        dry_run=dry_run,
    )
