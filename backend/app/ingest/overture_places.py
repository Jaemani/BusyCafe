"""Operator-run Overture Places cache ingestion utilities.

This module is intentionally outside the request path.  It reads a bounded
Seoul extract, validates records and materializes them into our database so
map viewport requests are local indexed queries only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from math import floor, isclose, isfinite
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    OVERTURE_CAFE_CATEGORIES,
    OVERTURE_CONFIDENCE_ABS_TOL,
    OVERTURE_CONFIDENCE_REPORT_MAX,
    OVERTURE_CONFIDENCE_REPORT_MIN,
    OVERTURE_CONFIDENCE_REPORT_STEP,
    OVERTURE_MIN_CONFIDENCE,
    OVERTURE_COORDINATE_ABS_TOL_DEG,
    OVERTURE_S3_URI_TEMPLATE,
    SEOUL_BBOX,
)
from app.geo import haversine_m
from app.models import Cafe, CafeProviderPlace


class OvertureIngestError(ValueError):
    """A bounded Overture extract cannot safely be materialized."""


_NAVER_MAP_DETAIL_PATH = re.compile(r"^/p/entry/place/([0-9]+)/?$")
_NAVER_MOBILE_DETAIL_PATH = re.compile(
    r"^/(place|restaurant)/([0-9]+)(?:/(?:home|menu|review|photo))?/?$"
)
_KAKAO_DETAIL_PATH = re.compile(r"^/([0-9]+)/?$")


def direct_website_provider_reference(
    website: str | None,
) -> tuple[str, str, str] | None:
    """Extract only exact provider detail identities from an Overture website."""

    if not website:
        return None
    parsed = urlparse(website.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.hostname
    if host in {"map.naver.com", "m.map.naver.com"}:
        matched = _NAVER_MAP_DETAIL_PATH.fullmatch(parsed.path)
        if matched:
            place_id = matched.group(1)
            return (
                "naver",
                place_id,
                f"https://map.naver.com/p/entry/place/{place_id}",
            )
        return None
    if host == "m.place.naver.com":
        matched = _NAVER_MOBILE_DETAIL_PATH.fullmatch(parsed.path)
        if matched:
            place_type, place_id = matched.groups()
            return (
                "naver",
                place_id,
                f"https://m.place.naver.com/{place_type}/{place_id}",
            )
        return None
    if host == "place.map.kakao.com":
        matched = _KAKAO_DETAIL_PATH.fullmatch(parsed.path)
        if matched:
            place_id = matched.group(1)
            return (
                "kakao",
                place_id,
                f"https://place.map.kakao.com/{place_id}",
            )
        return None
    if host in {"www.google.com", "maps.google.com", "google.com"}:
        query = parse_qs(parsed.query)
        place_ids = query.get("query_place_id") or query.get("cid")
        if place_ids and len(place_ids) == 1 and place_ids[0].strip():
            place_id = place_ids[0].strip()
            return "google", place_id, website.strip()
    return None


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
class NumericDeltaSummary:
    count: int
    minimum: float
    p50: float
    p95: float
    maximum: float


def summarize_numeric_deltas(values: Iterable[float]) -> NumericDeltaSummary | None:
    """Return deterministic linear-percentile diagnostics for non-negative deltas."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if any(not isfinite(value) or value < 0 for value in ordered):
        raise ValueError("numeric deltas must be finite and non-negative")

    def percentile(fraction: float) -> float:
        rank = (len(ordered) - 1) * fraction
        lower = floor(rank)
        upper = min(lower + 1, len(ordered) - 1)
        weight = rank - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return NumericDeltaSummary(
        count=len(ordered),
        minimum=ordered[0],
        p50=percentile(0.50),
        p95=percentile(0.95),
        maximum=ordered[-1],
    )


@dataclass(frozen=True, slots=True)
class OvertureSeedReport:
    source_count: int
    inserted_count: int
    updated_count: int
    unchanged_count: int
    deactivated_count: int
    provider_deactivated_count: int
    active_count: int
    dry_run: bool
    changed_field_counts: tuple[tuple[str, int], ...] = ()
    coordinate_delta_m: NumericDeltaSummary | None = None
    confidence_abs_delta: NumericDeltaSummary | None = None


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


def overture_seed_value_equal(
    field: str,
    existing: object,
    incoming: object,
    *,
    coordinate_abs_tol_deg: float = OVERTURE_COORDINATE_ABS_TOL_DEG,
    confidence_abs_tol: float = OVERTURE_CONFIDENCE_ABS_TOL,
) -> bool:
    """Compare seed fields, tolerating only bounded storage float jitter."""

    if field in {"lat", "lng"}:
        return isclose(
            float(existing),
            float(incoming),
            rel_tol=0.0,
            abs_tol=coordinate_abs_tol_deg,
        )
    if field == "source_confidence":
        return isclose(
            float(existing),
            float(incoming),
            rel_tol=0.0,
            abs_tol=confidence_abs_tol,
        )
    return existing == incoming


def _validate_scope_bbox(
    scope_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Validate an inclusive WGS84 point scope used for one seed operation."""

    min_lng, min_lat, max_lng, max_lat = scope_bbox
    if not all(isfinite(value) for value in scope_bbox):
        raise OvertureIngestError("scope bbox coordinates must be finite")
    if not (-180 <= min_lng < max_lng <= 180):
        raise OvertureIngestError("scope bbox longitude bounds are invalid")
    if not (-90 <= min_lat < max_lat <= 90):
        raise OvertureIngestError("scope bbox latitude bounds are invalid")
    return min_lng, min_lat, max_lng, max_lat


def _record_is_in_scope(
    record: OvertureCafeRecord,
    *,
    scope_bbox: tuple[float, float, float, float],
) -> bool:
    """Return whether a point lies in the bbox, including all four edges."""

    min_lng, min_lat, max_lng, max_lat = scope_bbox
    return min_lng <= record.lng <= max_lng and min_lat <= record.lat <= max_lat


def _record_provider_references(
    record: OvertureCafeRecord,
) -> tuple[tuple[str, str, str | None, str], ...]:
    references: list[tuple[str, str, str | None, str]] = [
        ("overture", record.overture_id, None, "source_primary")
    ]
    direct = direct_website_provider_reference(record.website)
    if direct is not None:
        provider, provider_place_id, detail_url = direct
        references.append(
            (provider, provider_place_id, detail_url, "source_direct_url")
        )
    return tuple(references)


def seed_overture_cafes(
    session: Session,
    records: Iterable[OvertureCafeRecord],
    *,
    release: str,
    scope_bbox: tuple[float, float, float, float],
    dry_run: bool = False,
) -> OvertureSeedReport:
    """Materialize one release inside an explicit, edge-inclusive point bbox.

    Missing-record deactivation is limited to cafes whose stored point lies in
    the same bbox. Every input record must also lie inside it; mixed or wrongly
    bounded extracts fail before any database mutation.
    """

    validated_scope = _validate_scope_bbox(scope_bbox)

    records_by_id: dict[str, OvertureCafeRecord] = {}
    incoming_provider_owners: dict[tuple[str, str], str] = {}
    for record in records:
        if not _record_is_in_scope(record, scope_bbox=validated_scope):
            raise OvertureIngestError(
                f"Overture record is outside seed scope: {record.overture_id}"
            )
        if record.overture_id in records_by_id:
            raise OvertureIngestError(f"duplicate Overture ID: {record.overture_id}")
        records_by_id[record.overture_id] = record
        for provider, provider_place_id, _, _ in _record_provider_references(record):
            key = (provider, provider_place_id)
            previous_owner = incoming_provider_owners.setdefault(
                key, record.overture_id
            )
            if previous_owner != record.overture_id:
                raise OvertureIngestError(
                    f"duplicate {provider} provider place ID in seed input"
                )
    if not records_by_id:
        raise OvertureIngestError("refusing to replace the POI cache with an empty extract")

    min_lng, min_lat, max_lng, max_lat = validated_scope
    existing_query = select(Cafe).where(
        Cafe.lng >= min_lng,
        Cafe.lng <= max_lng,
        Cafe.lat >= min_lat,
        Cafe.lat <= max_lat,
    )
    scoped_cafes = tuple(session.scalars(existing_query))
    existing_by_id = {
        cafe.overture_id: cafe
        for cafe in scoped_cafes
        if cafe.overture_id is not None
    }
    overture_owned_by_id = {
        cafe.overture_id: cafe
        for cafe in scoped_cafes
        if cafe.origin_provider == "overture" and cafe.overture_id is not None
    }
    provider_places = tuple(session.scalars(select(CafeProviderPlace)))
    provider_by_key = {
        (place.provider, place.provider_place_id): place
        for place in provider_places
    }
    provider_by_cafe_provider = {
        (place.cafe_id, place.provider): place for place in provider_places
    }
    provider_places_by_cafe: dict[int, list[CafeProviderPlace]] = {}
    for place in provider_places:
        provider_places_by_cafe.setdefault(place.cafe_id, []).append(place)
    seen_at = datetime.now(UTC)

    def sync_provider_places(
        cafe: Cafe | None,
        references: tuple[tuple[str, str, str | None, str], ...],
    ) -> list[CafeProviderPlace]:
        new_places: list[CafeProviderPlace] = []
        for provider, provider_place_id, detail_url, match_method in references:
            owner = provider_by_key.get((provider, provider_place_id))
            if cafe is None:
                if owner is not None:
                    raise OvertureIngestError(
                        f"{provider} provider place ID already belongs to another cafe"
                    )
                new_places.append(
                    CafeProviderPlace(
                        provider=provider,
                        provider_place_id=provider_place_id,
                        detail_url=detail_url,
                        active=True,
                        match_method=match_method,
                        verified_at=seen_at,
                        last_seen_at=seen_at,
                    )
                )
                continue
            if owner is not None and owner.cafe_id != cafe.id:
                raise OvertureIngestError(
                    f"{provider} provider place ID already belongs to another cafe"
                )
            current = provider_by_cafe_provider.get((cafe.id, provider))
            if current is not None and current.provider_place_id != provider_place_id:
                if (
                    cafe.origin_provider == "overture"
                    and current.match_method == "source_direct_url"
                    and match_method == "source_direct_url"
                ):
                    if not dry_run:
                        provider_by_key.pop(
                            (current.provider, current.provider_place_id), None
                        )
                        current.provider_place_id = provider_place_id
                        current.detail_url = detail_url
                        current.active = True
                        current.verified_at = seen_at
                        current.last_seen_at = seen_at
                        provider_by_key[(provider, provider_place_id)] = current
                    continue
                raise OvertureIngestError(
                    f"cafe already has a different {provider} provider place ID"
                )
            if current is None:
                new_places.append(
                    CafeProviderPlace(
                        cafe_id=cafe.id,
                        provider=provider,
                        provider_place_id=provider_place_id,
                        detail_url=detail_url,
                        active=True,
                        match_method=match_method,
                        verified_at=seen_at,
                        last_seen_at=seen_at,
                    )
                )
            elif (
                match_method == "source_direct_url"
                and current.match_method != "source_direct_url"
            ):
                # Same identity is already owned by provider-catalog matching.
                # Overture website ingestion must not refresh or reactivate it.
                continue
            elif not dry_run:
                current.detail_url = detail_url
                current.active = True
                current.last_seen_at = seen_at
        return new_places

    def deactivate_removed_source_direct(
        cafe: Cafe,
        references: tuple[tuple[str, str, str | None, str], ...],
    ) -> int:
        if cafe.origin_provider != "overture":
            return 0
        incoming_by_provider = {
            provider: provider_place_id
            for provider, provider_place_id, _, match_method in references
            if match_method == "source_direct_url"
        }
        deactivated = 0
        for place in provider_places_by_cafe.get(cafe.id, ()):
            if (
                place.active
                and place.match_method == "source_direct_url"
                and place.provider not in incoming_by_provider
            ):
                deactivated += 1
                if not dry_run:
                    place.active = False
        return deactivated

    inserted_count = updated_count = unchanged_count = deactivated_count = 0
    provider_deactivated_count = 0
    changed_field_counts: dict[str, int] = {}
    coordinate_deltas_m: list[float] = []
    confidence_abs_deltas: list[float] = []
    for overture_id in sorted(records_by_id):
        record = records_by_id[overture_id]
        values = _values(record, release=release)
        existing = existing_by_id.get(overture_id)
        references = _record_provider_references(record)
        if existing is None:
            inserted_count += 1
            new_provider_places = sync_provider_places(None, references)
            if not dry_run:
                session.add(
                    Cafe(
                        overture_id=overture_id,
                        origin_provider="overture",
                        origin_source_id=overture_id,
                        provider_places=new_provider_places,
                        **values,
                    )
                )
            continue
        new_provider_places = sync_provider_places(existing, references)
        if not dry_run:
            session.add_all(new_provider_places)
        if existing.origin_provider != "overture":
            unchanged_count += 1
            continue
        provider_deactivated_count += deactivate_removed_source_direct(
            existing, references
        )
        changed_fields = tuple(
            key
            for key, value in values.items()
            if not overture_seed_value_equal(key, getattr(existing, key), value)
        )
        if not changed_fields:
            unchanged_count += 1
            continue
        updated_count += 1
        for key in changed_fields:
            changed_field_counts[key] = changed_field_counts.get(key, 0) + 1
        if "lat" in changed_fields or "lng" in changed_fields:
            coordinate_deltas_m.append(
                haversine_m(existing.lat, existing.lng, record.lat, record.lng)
            )
        if "source_confidence" in changed_fields:
            confidence_abs_deltas.append(
                abs(existing.source_confidence - record.confidence)
            )
        if not dry_run:
            for key in changed_fields:
                value = values[key]
                setattr(existing, key, value)

    for overture_id, existing in overture_owned_by_id.items():
        if overture_id in records_by_id:
            continue
        if existing.active:
            deactivated_count += 1
            if not dry_run:
                existing.active = False
                provider_place = provider_by_cafe_provider.get(
                    (existing.id, "overture")
                )
                if provider_place is not None:
                    provider_place.active = False
        provider_deactivated_count += deactivate_removed_source_direct(
            existing, ()
        )
    if not dry_run:
        session.commit()

    return OvertureSeedReport(
        source_count=len(records_by_id),
        inserted_count=inserted_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        deactivated_count=deactivated_count,
        provider_deactivated_count=provider_deactivated_count,
        active_count=len(records_by_id),
        dry_run=dry_run,
        changed_field_counts=tuple(sorted(changed_field_counts.items())),
        coordinate_delta_m=summarize_numeric_deltas(coordinate_deltas_m),
        confidence_abs_delta=summarize_numeric_deltas(confidence_abs_deltas),
    )


# --- Confidence-threshold study (`--confidence-report`) -------------------
#
# Read-only, no network: `cache_seoul_extract` always applies a hard
# `confidence >= min_confidence` SQL filter *before* writing the local
# extract, and the extract itself does not persist which threshold was used.
# So a local cache can never prove "there are zero cafes below 0.80" — it can
# only ever show "zero cafes below whatever floor this extract was
# downloaded with". We approximate that floor as the lowest confidence value
# actually observed in the extract and flag every bucket entirely below it
# as filtered-not-zero, rather than silently printing 0.


@dataclass(frozen=True, slots=True)
class ConfidenceBucket:
    lower: float
    upper: float
    count: int
    cache_filtered: bool


@dataclass(frozen=True, slots=True)
class ConfidenceReport:
    total_count: int
    threshold: float
    passing_count: int
    observed_min_confidence: float | None
    cache_pre_filtered: bool
    below_range_count: int
    buckets: tuple[ConfidenceBucket, ...]
    category_counts: dict[str, int]


def _confidence_bucket_edges(
    *,
    lower: float = OVERTURE_CONFIDENCE_REPORT_MIN,
    upper: float = OVERTURE_CONFIDENCE_REPORT_MAX,
    step: float = OVERTURE_CONFIDENCE_REPORT_STEP,
) -> tuple[float, ...]:
    step_count = round((upper - lower) / step)
    return tuple(round(lower + step * i, 2) for i in range(step_count + 1))


def build_confidence_report(
    records: Iterable[OvertureCafeRecord],
    *,
    threshold: float = OVERTURE_MIN_CONFIDENCE,
) -> ConfidenceReport:
    """Compute a read-only confidence distribution over already-loaded records.

    Pure function: never opens the network or a DB session. Callers pass
    records already produced by `iter_cached_records` (local file only).
    """

    edges = _confidence_bucket_edges()
    bucket_count = len(edges) - 1
    counts = [0] * bucket_count
    below_range_count = 0
    total_count = 0
    passing_count = 0
    observed_min: float | None = None
    category_counts: dict[str, int] = {}

    # Tolerate float round-trip noise (e.g. (0.95 - 0.50) / 0.05 evaluating to
    # 8.999999999999998 instead of 9.0) both when bucketing a confidence value
    # and when comparing the observed floor to bucket edges below.
    epsilon = 1e-9

    for record in records:
        total_count += 1
        confidence = record.confidence
        observed_min = confidence if observed_min is None else min(observed_min, confidence)
        if confidence >= threshold:
            passing_count += 1
        category_counts[record.primary_category] = (
            category_counts.get(record.primary_category, 0) + 1
        )

        if confidence < edges[0]:
            below_range_count += 1
            continue
        index = min(
            int((confidence - edges[0]) / OVERTURE_CONFIDENCE_REPORT_STEP + epsilon),
            bucket_count - 1,
        )
        counts[index] += 1

    cache_pre_filtered = observed_min is not None and observed_min > edges[0] + epsilon

    buckets = tuple(
        ConfidenceBucket(
            lower=edges[index],
            upper=edges[index + 1],
            count=counts[index],
            cache_filtered=(
                cache_pre_filtered
                and observed_min is not None
                and edges[index + 1] <= observed_min + epsilon
            ),
        )
        for index in range(bucket_count)
    )

    return ConfidenceReport(
        total_count=total_count,
        threshold=threshold,
        passing_count=passing_count,
        observed_min_confidence=observed_min,
        cache_pre_filtered=cache_pre_filtered,
        below_range_count=below_range_count,
        buckets=buckets,
        category_counts=category_counts,
    )


def format_confidence_report(report: ConfidenceReport, *, cache_path: Path) -> str:
    """Render a stable human-readable confidence-distribution report."""

    lines = [
        "Overture confidence report (read-only, no DB writes, no network)",
        f"cache: {cache_path}",
        f"records in cache: {report.total_count}",
        f"pass current threshold (>= {report.threshold:.2f}): {report.passing_count}/{report.total_count}",
    ]
    if report.observed_min_confidence is not None:
        lines.append(f"observed min confidence in cache: {report.observed_min_confidence:.4f}")
    if report.cache_pre_filtered:
        lines.append(
            "NOTE: this cache was produced by a download-time confidence filter "
            f"(cache_seoul_extract's SQL WHERE clause) at ~{report.observed_min_confidence:.2f}. "
            "Bucket counts below that floor are NOT zero real candidates — the "
            "extract never contained them. Re-download with a lower "
            "--min-confidence to study that range."
        )
    if report.below_range_count:
        lines.append(
            f"below {OVERTURE_CONFIDENCE_REPORT_MIN:.2f} (out of report range): "
            f"{report.below_range_count}"
        )
    lines.append("buckets:")
    for bucket in report.buckets:
        label = "cache filtered, re-download required" if bucket.cache_filtered else str(bucket.count)
        lines.append(f"- [{bucket.lower:.2f}, {bucket.upper:.2f}): {label}")
    lines.append("by category:")
    for category in sorted(report.category_counts):
        lines.append(f"- {category}: {report.category_counts[category]}")
    return "\n".join(lines)
