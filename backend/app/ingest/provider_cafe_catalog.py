"""Pure reconciliation for provider links and permit-origin cafe candidates.

The builder deliberately keeps provider discovery separate from persistence.
It accepts already-cached inputs, reuses the conservative one-to-one matcher,
and emits only the minimum provider fields needed for a direct detail link.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from urllib.parse import urlparse

from app.config import KAKAO_CAFE_CATEGORY_CODE, SEOUL_BBOX
from app.ingest.overture_places import OvertureCafeRecord, OvertureIngestError
from app.ingest.permit_reconciliation import CatalogPlace, reconcile_candidates
from app.ingest.seoul_refreshment_candidates import PlaceCandidate
from app.schemas import KakaoPlace


KAKAO_PROVIDER = "kakao"
KAKAO_SOURCE = "kakao_local"
OVERTURE_SOURCE = "overture"
PERMIT_SOURCE = "seoul_refreshment_permits"
PERMIT_DATASET_ID = "OA-16095"
PERMIT_PROVENANCE = "official_open_refreshment_permit"
PROVIDER_CATALOG_SCHEMA_VERSION = "provider-cafe-catalog-v1"
_NAVER_MAP_DETAIL_PATH = re.compile(r"^/p/entry/place/([0-9]+)/?$")
_NAVER_MOBILE_DETAIL_PATH = re.compile(
    r"^/(place|restaurant)/([0-9]+)(?:/(?:home|menu|review|photo))?/?$"
)


class ProviderCatalogError(ValueError):
    """Raised when cached provider inputs cannot be reconciled safely."""


@dataclass(frozen=True, slots=True)
class ProviderReference:
    """One verified direct provider detail link for a canonical place."""

    canonical_source: str
    canonical_source_id: str
    provider: str
    provider_place_id: str
    direct_url: str
    match_rule: str
    match_distance_m: float | None


@dataclass(frozen=True, slots=True)
class ProviderNeutralCafeCandidate:
    """Permit-owned cafe candidate confirmed by an independent POI provider."""

    canonical_source: str
    canonical_source_id: str
    name: str
    latitude: float
    longitude: float
    category: str
    road_address: str | None
    lot_address: str | None
    phone: str | None
    provider_refs: tuple[ProviderReference, ...]


@dataclass(frozen=True, slots=True)
class ProviderCatalogReport:
    overture_input_count: int
    permit_input_count: int
    kakao_input_count: int
    existing_permit_annotation_count: int
    permit_excluded_as_existing_count: int
    overture_naver_direct_count: int
    overture_kakao_match_count: int
    overture_kakao_ambiguous_count: int
    overture_kakao_unmatched_count: int
    permit_kakao_candidate_count: int
    permit_kakao_catalog_count: int
    permit_kakao_match_count: int
    permit_kakao_ambiguous_count: int
    permit_kakao_unmatched_count: int
    new_cafe_candidate_count: int


@dataclass(frozen=True, slots=True)
class ProviderCatalogBuild:
    existing_provider_refs: tuple[ProviderReference, ...]
    new_cafe_candidates: tuple[ProviderNeutralCafeCandidate, ...]
    report: ProviderCatalogReport


@dataclass(frozen=True, slots=True)
class ProviderCatalogRecords:
    """Validated provider cache records ready for persistence."""

    existing_provider_refs: tuple[ProviderReference, ...]
    new_cafe_candidates: tuple[ProviderNeutralCafeCandidate, ...]


def _kakao_direct_url(place_id: str) -> str:
    if not place_id.isascii() or not place_id.isdigit():
        raise ProviderCatalogError("Kakao place ID must contain ASCII digits only")
    return f"https://place.map.kakao.com/{place_id}"


def _kakao_candidates(
    places: Sequence[KakaoPlace],
) -> tuple[tuple[PlaceCandidate, ...], dict[str, KakaoPlace]]:
    by_id: dict[str, KakaoPlace] = {}
    for place in places:
        if place.place_id in by_id:
            raise ProviderCatalogError(f"duplicate Kakao place ID: {place.place_id}")
        if place.category_group_code != KAKAO_CAFE_CATEGORY_CODE:
            raise ProviderCatalogError(
                f"Kakao place is not CE7: {place.place_id}"
            )
        _kakao_direct_url(place.place_id)
        by_id[place.place_id] = place
    candidates = tuple(
        PlaceCandidate(
            source=KAKAO_SOURCE,
            source_id=place.place_id,
            name=place.place_name,
            latitude=place.latitude,
            longitude=place.longitude,
            category=place.category_name or place.category_group_code,
            road_address=place.road_address_name or None,
            lot_address=place.address_name or None,
            phone=place.phone or None,
        )
        for place in sorted(by_id.values(), key=lambda value: value.place_id)
    )
    return candidates, by_id


def _overture_catalog(
    records: Sequence[OvertureCafeRecord],
) -> tuple[CatalogPlace, ...]:
    seen: set[str] = set()
    catalog: list[CatalogPlace] = []
    for record in sorted(records, key=lambda value: value.overture_id):
        if record.overture_id in seen:
            raise OvertureIngestError(f"duplicate Overture ID: {record.overture_id}")
        seen.add(record.overture_id)
        catalog.append(
            CatalogPlace(
                catalog_id=record.overture_id,
                name=record.name,
                latitude=record.lat,
                longitude=record.lng,
                category=record.primary_category,
                phone=record.phone,
            )
        )
    return tuple(catalog)


def _existing_permit_ids(records: Sequence[OvertureCafeRecord]) -> frozenset[str]:
    management_numbers: set[str] = set()
    for record in records:
        for source in record.sources:
            if (
                source.get("dataset_id") != PERMIT_DATASET_ID
                or source.get("provenance") != PERMIT_PROVENANCE
            ):
                continue
            management_number = source.get("management_number")
            if isinstance(management_number, str) and management_number.strip():
                management_numbers.add(management_number.strip())
    return frozenset(management_numbers)


def _provider_ref(
    *,
    canonical_source: str,
    canonical_source_id: str,
    kakao_place_id: str,
    match_rule: str,
    distance_m: float,
) -> ProviderReference:
    return ProviderReference(
        canonical_source=canonical_source,
        canonical_source_id=canonical_source_id,
        provider=KAKAO_PROVIDER,
        provider_place_id=kakao_place_id,
        direct_url=_kakao_direct_url(kakao_place_id),
        match_rule=match_rule,
        match_distance_m=round(distance_m, 3),
    )


def _overture_naver_ref(
    record: OvertureCafeRecord,
) -> ProviderReference | None:
    """Extract only an exact Naver place-detail identity from source website."""

    if not record.website:
        return None
    parsed = urlparse(record.website.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    # Reject user-info and non-default/custom ports rather than canonicalizing
    # a URL whose authority is not exactly the allow-listed hostname.
    if parsed.netloc.lower() != parsed.hostname.lower():
        return None

    place_id: str | None = None
    canonical_url: str | None = None
    if parsed.hostname in {"map.naver.com", "m.map.naver.com"}:
        matched = _NAVER_MAP_DETAIL_PATH.fullmatch(parsed.path)
        if matched:
            place_id = matched.group(1)
            canonical_url = f"https://map.naver.com/p/entry/place/{place_id}"
    elif parsed.hostname == "m.place.naver.com":
        matched = _NAVER_MOBILE_DETAIL_PATH.fullmatch(parsed.path)
        if matched:
            place_type, place_id = matched.groups()
            canonical_url = (
                f"https://m.place.naver.com/{place_type}/{place_id}"
            )
    if place_id is None or canonical_url is None:
        return None
    return ProviderReference(
        canonical_source=OVERTURE_SOURCE,
        canonical_source_id=record.overture_id,
        provider="naver",
        provider_place_id=place_id,
        direct_url=canonical_url,
        match_rule="source_direct_url",
        match_distance_m=None,
    )


def build_provider_cafe_catalog(
    overture_records: Sequence[OvertureCafeRecord],
    permit_candidates: Sequence[PlaceCandidate],
    kakao_places: Sequence[KakaoPlace],
) -> ProviderCatalogBuild:
    """Reconcile cached sources without network, persistence, or fuzzy matching."""

    kakao_candidates, kakao_by_id = _kakao_candidates(kakao_places)
    overture_catalog = _overture_catalog(overture_records)

    overture_matches = reconcile_candidates(kakao_candidates, overture_catalog)
    kakao_refs = tuple(
        _provider_ref(
            canonical_source=OVERTURE_SOURCE,
            canonical_source_id=match.catalog.catalog_id,
            kakao_place_id=match.candidate.source_id,
            match_rule=match.rule,
            distance_m=match.distance_m,
        )
        for match in overture_matches.matches
    )
    naver_refs = tuple(
        reference
        for record in overture_records
        if (reference := _overture_naver_ref(record)) is not None
    )
    provider_owners: dict[tuple[str, str], str] = {}
    for reference in (*kakao_refs, *naver_refs):
        key = (reference.provider, reference.provider_place_id)
        previous_owner = provider_owners.setdefault(
            key, reference.canonical_source_id
        )
        if previous_owner != reference.canonical_source_id:
            raise ProviderCatalogError(
                f"duplicate {reference.provider} provider place ID"
            )
    existing_refs = tuple(
        sorted(
            (*kakao_refs, *naver_refs),
            key=lambda ref: (
                ref.canonical_source,
                ref.canonical_source_id,
                ref.provider,
                ref.provider_place_id,
            ),
        )
    )

    existing_permit_ids = _existing_permit_ids(overture_records)
    seen_permits: set[str] = set()
    eligible_permits: list[PlaceCandidate] = []
    excluded_existing_count = 0
    for permit in sorted(permit_candidates, key=lambda value: value.source_id):
        if permit.source != PERMIT_SOURCE:
            raise ProviderCatalogError(
                f"unexpected permit source: {permit.source}"
            )
        if permit.source_id in seen_permits:
            raise ProviderCatalogError(
                f"duplicate permit source ID: {permit.source_id}"
            )
        seen_permits.add(permit.source_id)
        if permit.source_id in existing_permit_ids:
            excluded_existing_count += 1
        else:
            eligible_permits.append(permit)

    matched_kakao_ids = {
        match.candidate.source_id for match in overture_matches.matches
    }
    ambiguous_kakao_ids = {
        candidate.source_id for candidate in overture_matches.ambiguous
    }
    safe_unmatched_kakao = tuple(
        candidate
        for candidate in overture_matches.unmatched
        if candidate.source_id not in ambiguous_kakao_ids
        and candidate.source_id not in matched_kakao_ids
    )
    kakao_catalog = tuple(
        CatalogPlace(
            catalog_id=candidate.source_id,
            name=candidate.name,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            category=candidate.category,
            phone=candidate.phone,
        )
        for candidate in safe_unmatched_kakao
    )
    permit_matches = reconcile_candidates(tuple(eligible_permits), kakao_catalog)

    new_candidates: list[ProviderNeutralCafeCandidate] = []
    for match in permit_matches.matches:
        permit = match.candidate
        kakao_place = kakao_by_id[match.catalog.catalog_id]
        provider_ref = _provider_ref(
            canonical_source=PERMIT_SOURCE,
            canonical_source_id=permit.source_id,
            kakao_place_id=kakao_place.place_id,
            match_rule=match.rule,
            distance_m=match.distance_m,
        )
        new_candidates.append(
            ProviderNeutralCafeCandidate(
                canonical_source=PERMIT_SOURCE,
                canonical_source_id=permit.source_id,
                name=permit.name,
                latitude=permit.latitude,
                longitude=permit.longitude,
                category=permit.category,
                road_address=permit.road_address,
                lot_address=permit.lot_address,
                phone=permit.phone,
                provider_refs=(provider_ref,),
            )
        )
    new_candidates_tuple = tuple(
        sorted(new_candidates, key=lambda item: item.canonical_source_id)
    )

    return ProviderCatalogBuild(
        existing_provider_refs=existing_refs,
        new_cafe_candidates=new_candidates_tuple,
        report=ProviderCatalogReport(
            overture_input_count=len(overture_records),
            permit_input_count=len(permit_candidates),
            kakao_input_count=len(kakao_places),
            existing_permit_annotation_count=len(existing_permit_ids),
            permit_excluded_as_existing_count=excluded_existing_count,
            overture_naver_direct_count=len(naver_refs),
            overture_kakao_match_count=len(overture_matches.matches),
            overture_kakao_ambiguous_count=len(overture_matches.ambiguous),
            overture_kakao_unmatched_count=len(overture_matches.unmatched),
            permit_kakao_candidate_count=len(eligible_permits),
            permit_kakao_catalog_count=len(kakao_catalog),
            permit_kakao_match_count=len(permit_matches.matches),
            permit_kakao_ambiguous_count=len(permit_matches.ambiguous),
            permit_kakao_unmatched_count=len(permit_matches.unmatched),
            new_cafe_candidate_count=len(new_candidates_tuple),
        ),
    )


def _provider_ref_payload(reference: ProviderReference) -> dict[str, object]:
    return {
        "canonical_source": reference.canonical_source,
        "canonical_source_id": reference.canonical_source_id,
        "direct_url": reference.direct_url,
        "match_distance_m": reference.match_distance_m,
        "match_rule": reference.match_rule,
        "provider": reference.provider,
        "provider_place_id": reference.provider_place_id,
    }


def serialize_provider_catalog(build: ProviderCatalogBuild) -> bytes:
    """Serialize a stable JSONL cache containing no raw Kakao response fields."""

    payloads: list[dict[str, object]] = [
        {"record_type": "provider_ref", **_provider_ref_payload(reference)}
        for reference in build.existing_provider_refs
    ]
    payloads.extend(
        {
            "record_type": "cafe_candidate",
            "canonical_source": candidate.canonical_source,
            "canonical_source_id": candidate.canonical_source_id,
            "name": candidate.name,
            "latitude": candidate.latitude,
            "longitude": candidate.longitude,
            "category": candidate.category,
            "road_address": candidate.road_address,
            "lot_address": candidate.lot_address,
            "phone": candidate.phone,
            "provider_refs": [
                _provider_ref_payload(reference)
                for reference in candidate.provider_refs
            ],
        }
        for candidate in build.new_cafe_candidates
    )
    lines = [
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for payload in payloads
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def build_provider_catalog_manifest(
    build: ProviderCatalogBuild,
    cache_bytes: bytes,
) -> dict[str, object]:
    """Return a deterministic aggregate manifest for one serialized cache."""

    return {
        "schema_version": PROVIDER_CATALOG_SCHEMA_VERSION,
        "complete": True,
        "cache_sha256": hashlib.sha256(cache_bytes).hexdigest(),
        "cache_size_bytes": len(cache_bytes),
        "record_count": (
            len(build.existing_provider_refs) + len(build.new_cafe_candidates)
        ),
        "report": asdict(build.report),
    }


def serialize_provider_catalog_manifest(
    build: ProviderCatalogBuild,
    cache_bytes: bytes,
) -> bytes:
    """Serialize the aggregate manifest with stable key ordering."""

    return (
        json.dumps(
            build_provider_catalog_manifest(build, cache_bytes),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _required_text(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderCatalogError(f"{field} must be non-empty text")
    return value


def _optional_text(payload: dict[str, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderCatalogError(f"{field} must be null or non-empty text")
    return value


def _number(payload: dict[str, object], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderCatalogError(f"{field} must be a finite number")
    converted = float(value)
    if not isfinite(converted):
        raise ProviderCatalogError(f"{field} must be a finite number")
    return converted


def _parse_provider_reference(payload: object) -> ProviderReference:
    if not isinstance(payload, dict):
        raise ProviderCatalogError("provider reference must be a JSON object")
    expected = {
        "canonical_source",
        "canonical_source_id",
        "direct_url",
        "match_distance_m",
        "match_rule",
        "provider",
        "provider_place_id",
    }
    if set(payload) != expected:
        raise ProviderCatalogError("provider reference fields do not match contract")
    canonical_source = _required_text(payload, "canonical_source")
    canonical_source_id = _required_text(payload, "canonical_source_id")
    provider = _required_text(payload, "provider")
    provider_place_id = _required_text(payload, "provider_place_id")
    direct_url = _required_text(payload, "direct_url")
    match_rule = _required_text(payload, "match_rule")
    raw_distance = payload.get("match_distance_m")
    distance = (
        None
        if raw_distance is None
        else _number(payload, "match_distance_m")
    )
    if distance is not None and distance < 0:
        raise ProviderCatalogError("match_distance_m must be non-negative")
    if canonical_source not in {OVERTURE_SOURCE, PERMIT_SOURCE}:
        raise ProviderCatalogError("unsupported canonical source")
    if provider == KAKAO_PROVIDER:
        if distance is None:
            raise ProviderCatalogError("Kakao match distance is required")
        if direct_url != _kakao_direct_url(provider_place_id):
            raise ProviderCatalogError("Kakao direct URL does not match place ID")
    elif provider == "naver":
        parsed = urlparse(direct_url)
        extracted_id: str | None = None
        if parsed.netloc in {"map.naver.com", "m.map.naver.com"}:
            matched = _NAVER_MAP_DETAIL_PATH.fullmatch(parsed.path)
            if matched is not None:
                extracted_id = matched.group(1)
        elif parsed.netloc == "m.place.naver.com":
            matched = _NAVER_MOBILE_DETAIL_PATH.fullmatch(parsed.path)
            if matched is not None:
                extracted_id = matched.group(2)
        if (
            parsed.scheme != "https"
            or parsed.query
            or parsed.fragment
            or extracted_id != provider_place_id
            or match_rule != "source_direct_url"
            or distance is not None
        ):
            raise ProviderCatalogError("Naver direct URL does not match place ID")
    else:
        raise ProviderCatalogError("unsupported provider")
    return ProviderReference(
        canonical_source=canonical_source,
        canonical_source_id=canonical_source_id,
        provider=provider,
        provider_place_id=provider_place_id,
        direct_url=direct_url,
        match_rule=match_rule,
        match_distance_m=distance,
    )


def read_provider_catalog(path: Path) -> ProviderCatalogRecords:
    """Read and strictly validate one immutable provider catalog JSONL."""

    references: list[ProviderReference] = []
    candidates: list[ProviderNeutralCafeCandidate] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ProviderCatalogError("record must be a JSON object")
                record_type = payload.get("record_type")
                if record_type == "provider_ref":
                    reference_payload = dict(payload)
                    reference_payload.pop("record_type")
                    references.append(_parse_provider_reference(reference_payload))
                    continue
                if record_type != "cafe_candidate":
                    raise ProviderCatalogError("unsupported provider catalog record type")
                expected = {
                    "record_type",
                    "canonical_source",
                    "canonical_source_id",
                    "name",
                    "latitude",
                    "longitude",
                    "category",
                    "road_address",
                    "lot_address",
                    "phone",
                    "provider_refs",
                }
                if set(payload) != expected:
                    raise ProviderCatalogError(
                        "cafe candidate fields do not match contract"
                    )
                canonical_source = _required_text(payload, "canonical_source")
                canonical_source_id = _required_text(payload, "canonical_source_id")
                if canonical_source != PERMIT_SOURCE:
                    raise ProviderCatalogError("new cafes must use permit origin")
                latitude = _number(payload, "latitude")
                longitude = _number(payload, "longitude")
                min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
                if not (
                    min_lng <= longitude <= max_lng
                    and min_lat <= latitude <= max_lat
                ):
                    raise ProviderCatalogError("cafe candidate is outside Seoul bbox")
                raw_refs = payload.get("provider_refs")
                if not isinstance(raw_refs, list) or not raw_refs:
                    raise ProviderCatalogError(
                        "cafe candidate requires provider references"
                    )
                candidate_refs = tuple(
                    _parse_provider_reference(item) for item in raw_refs
                )
                if any(
                    reference.canonical_source != canonical_source
                    or reference.canonical_source_id != canonical_source_id
                    for reference in candidate_refs
                ):
                    raise ProviderCatalogError(
                        "candidate provider reference owner does not match"
                    )
                candidates.append(
                    ProviderNeutralCafeCandidate(
                        canonical_source=canonical_source,
                        canonical_source_id=canonical_source_id,
                        name=_required_text(payload, "name"),
                        latitude=latitude,
                        longitude=longitude,
                        category=_required_text(payload, "category"),
                        road_address=_optional_text(payload, "road_address"),
                        lot_address=_optional_text(payload, "lot_address"),
                        phone=_optional_text(payload, "phone"),
                        provider_refs=candidate_refs,
                    )
                )
            except (json.JSONDecodeError, ProviderCatalogError) as exc:
                raise ProviderCatalogError(
                    f"invalid provider catalog line {line_number} "
                    f"({type(exc).__name__}): {exc}"
                ) from exc

    candidate_keys: set[tuple[str, str]] = set()
    provider_owners: dict[tuple[str, str], tuple[str, str]] = {}
    canonical_providers: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        candidate_key = (
            candidate.canonical_source,
            candidate.canonical_source_id,
        )
        if candidate_key in candidate_keys:
            raise ProviderCatalogError("duplicate cafe candidate origin")
        candidate_keys.add(candidate_key)
    all_references = [*references]
    for candidate in candidates:
        all_references.extend(candidate.provider_refs)
    for reference in all_references:
        owner = (reference.canonical_source, reference.canonical_source_id)
        provider_key = (reference.provider, reference.provider_place_id)
        previous_owner = provider_owners.setdefault(provider_key, owner)
        if previous_owner != owner:
            raise ProviderCatalogError("provider place ID has multiple owners")
        canonical_key = (*owner, reference.provider)
        if canonical_key in canonical_providers:
            raise ProviderCatalogError("canonical cafe has duplicate provider")
        canonical_providers.add(canonical_key)
    return ProviderCatalogRecords(
        existing_provider_refs=tuple(references),
        new_cafe_candidates=tuple(candidates),
    )


def read_complete_provider_catalog(
    path: Path,
    manifest_path: Path,
) -> ProviderCatalogRecords:
    """Read only a complete catalog bound to its adjacent aggregate manifest."""

    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderCatalogError(
            f"invalid or missing provider catalog manifest: {manifest_path}"
        ) from exc
    if not isinstance(manifest_payload, dict):
        raise ProviderCatalogError("provider catalog manifest must be a JSON object")
    if manifest_payload.get("complete") is not True:
        raise ProviderCatalogError("provider catalog manifest is incomplete")
    if manifest_payload.get("schema_version") != PROVIDER_CATALOG_SCHEMA_VERSION:
        raise ProviderCatalogError(
            "unsupported provider catalog manifest schema_version"
        )

    try:
        cache_bytes = path.read_bytes()
    except OSError as exc:
        raise ProviderCatalogError(
            f"invalid or missing provider catalog: {path}"
        ) from exc
    expected_size = manifest_payload.get("cache_size_bytes")
    if type(expected_size) is not int or expected_size < 0:
        raise ProviderCatalogError(
            "provider catalog manifest cache_size_bytes is invalid"
        )
    if expected_size != len(cache_bytes):
        raise ProviderCatalogError(
            "provider catalog size does not match manifest"
        )
    expected_digest = manifest_payload.get("cache_sha256")
    actual_digest = hashlib.sha256(cache_bytes).hexdigest()
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or not hmac.compare_digest(expected_digest, actual_digest)
    ):
        raise ProviderCatalogError(
            "provider catalog sha256 does not match manifest"
        )

    catalog = read_provider_catalog(path)
    actual_count = len(catalog.existing_provider_refs) + len(
        catalog.new_cafe_candidates
    )
    expected_count = manifest_payload.get("record_count")
    if type(expected_count) is not int or expected_count < 0:
        raise ProviderCatalogError(
            "provider catalog manifest record_count is invalid"
        )
    if expected_count != actual_count:
        raise ProviderCatalogError(
            "provider catalog record_count does not match manifest"
        )

    report = manifest_payload.get("report")
    if not isinstance(report, dict):
        raise ProviderCatalogError("provider catalog manifest report is invalid")
    required_report_counts = {
        "overture_naver_direct_count",
        "overture_kakao_match_count",
        "new_cafe_candidate_count",
    }
    report_counts: dict[str, int] = {}
    for field in required_report_counts:
        value = report.get(field)
        if type(value) is not int or value < 0:
            raise ProviderCatalogError(
                f"provider catalog manifest report {field} is invalid"
            )
        report_counts[field] = value
    if (
        report_counts["overture_naver_direct_count"]
        + report_counts["overture_kakao_match_count"]
        != len(catalog.existing_provider_refs)
        or report_counts["new_cafe_candidate_count"]
        != len(catalog.new_cafe_candidates)
    ):
        raise ProviderCatalogError(
            "provider catalog records do not match manifest report"
        )
    return catalog
