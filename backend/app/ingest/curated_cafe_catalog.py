"""Build an immutable, safely expanded Overture cafe staging catalog."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from app.config import OVERTURE_MIN_CONFIDENCE
from app.ingest.overture_places import (
    OvertureCafeRecord,
    OvertureIngestError,
    parse_overture_row,
)
from app.ingest.permit_reconciliation import CatalogPlace, reconcile_candidates
from app.ingest.seoul_refreshment_candidates import PlaceCandidate


@dataclass(frozen=True, slots=True)
class CuratedCatalogReport:
    overture_input_count: int
    permit_candidate_count: int
    high_confidence_count: int
    unique_strong_match_count: int
    incremental_low_confidence_count: int
    excluded_low_confidence_count: int
    curated_count: int
    annotated_count: int
    curated_category_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class CuratedCatalogBuild:
    records: tuple[OvertureCafeRecord, ...]
    report: CuratedCatalogReport


def _source_key(source: dict[str, object]) -> str:
    return json.dumps(
        source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _annotate(
    record: OvertureCafeRecord,
    *,
    management_number: str,
    match_rule: str,
    distance_m: float,
) -> OvertureCafeRecord:
    annotation: dict[str, object] = {
        "dataset_id": "OA-16095",
        "management_number": management_number,
        "provenance": "official_open_refreshment_permit",
        "match_rule": match_rule,
        "distance_m": round(distance_m, 3),
    }
    unique_sources = {_source_key(source): source for source in record.sources}
    unique_sources[_source_key(annotation)] = annotation
    return replace(
        record,
        sources=[unique_sources[key] for key in sorted(unique_sources)],
    )


def build_curated_catalog(
    overture_records: Sequence[OvertureCafeRecord],
    permit_candidates: Sequence[PlaceCandidate],
    *,
    confidence_threshold: float = OVERTURE_MIN_CONFIDENCE,
) -> CuratedCatalogBuild:
    """Keep the existing confidence gate and admit only verified low rows."""

    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be between zero and one")
    records_by_id: dict[str, OvertureCafeRecord] = {}
    for record in overture_records:
        if record.overture_id in records_by_id:
            raise OvertureIngestError(f"duplicate Overture ID: {record.overture_id}")
        records_by_id[record.overture_id] = record

    catalog = tuple(
        CatalogPlace(
            catalog_id=record.overture_id,
            name=record.name,
            latitude=record.lat,
            longitude=record.lng,
            category=record.primary_category,
            phone=record.phone,
        )
        for record in records_by_id.values()
    )
    reconciliation = reconcile_candidates(permit_candidates, catalog)
    match_by_overture_id = {
        match.catalog.catalog_id: match for match in reconciliation.matches
    }

    curated: list[OvertureCafeRecord] = []
    high_count = incremental_count = annotated_count = 0
    for overture_id in sorted(records_by_id):
        record = records_by_id[overture_id]
        match = match_by_overture_id.get(overture_id)
        is_high = record.confidence >= confidence_threshold
        if not is_high and match is None:
            continue
        if is_high:
            high_count += 1
        else:
            incremental_count += 1
        if match is not None:
            record = _annotate(
                record,
                management_number=match.candidate.source_id,
                match_rule=match.rule,
                distance_m=match.distance_m,
            )
            annotated_count += 1
        curated.append(record)

    categories = Counter(record.primary_category for record in curated)
    return CuratedCatalogBuild(
        records=tuple(curated),
        report=CuratedCatalogReport(
            overture_input_count=len(overture_records),
            permit_candidate_count=len(permit_candidates),
            high_confidence_count=high_count,
            unique_strong_match_count=len(reconciliation.matches),
            incremental_low_confidence_count=incremental_count,
            excluded_low_confidence_count=(
                len(overture_records) - high_count - incremental_count
            ),
            curated_count=len(curated),
            annotated_count=annotated_count,
            curated_category_counts=dict(sorted(categories.items())),
        ),
    )


def serialize_curated_records(records: Sequence[OvertureCafeRecord]) -> bytes:
    lines = [
        json.dumps(
            {
                "overture_id": record.overture_id,
                "name": record.name,
                "lat": record.lat,
                "lng": record.lng,
                "primary_category": record.primary_category,
                "confidence": record.confidence,
                "road_address": record.road_address,
                "phone": record.phone,
                "website": record.website,
                "sources": record.sources,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in records
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def iter_curated_records(path: Path) -> Iterable[OvertureCafeRecord]:
    """Yield validated generic records from one local immutable JSONL cache."""

    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise TypeError("record must be a JSON object")
                expected = {
                    "overture_id",
                    "name",
                    "lat",
                    "lng",
                    "primary_category",
                    "confidence",
                    "road_address",
                    "phone",
                    "website",
                    "sources",
                }
                if set(payload) != expected:
                    raise ValueError("record fields do not match curated contract")
                yield parse_overture_row(
                    {
                        **payload,
                        "sources_json": json.dumps(
                            payload["sources"],
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    }
                )
            except (TypeError, ValueError, json.JSONDecodeError, OvertureIngestError) as exc:
                raise OvertureIngestError(
                    f"invalid curated cache line {line_number} ({type(exc).__name__})"
                ) from exc
