"""Read-only map API backed exclusively by the local PostgreSQL cache."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from math import ceil, isfinite
from urllib.parse import parse_qs, quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, load_only, raiseload, selectinload

from app.config import (
    CAFE_SEARCH_BRAND_ALIASES,
    CAFE_SEARCH_DEFAULT_LIMIT,
    CAFE_SEARCH_MAX_LIMIT,
    CAFE_SEARCH_MAX_QUERY_LENGTH,
    CAFE_SEARCH_MIN_QUERY_LENGTH,
    CURRENT_DISPLAY_MAX_AGE_MIN,
    FRESHNESS_MAX_FUTURE_SKEW_MIN,
    MAX_BBOX_SPAN_DEG,
    MAX_CAFES_PER_VIEWPORT,
    NAVER_MAP_SEARCH_BASE_URL,
    OVERTURE_RELEASE,
    SEOUL_BBOX,
    STALE_WARN_MIN,
)
from app.database import get_db
from app.models import (
    Cafe,
    CafeScore,
    Hotspot,
    HotspotServingState,
    HotspotSnapshot,
    IngestCycle,
)
from app.schemas import (
    CafeDetailResponse,
    CafeMapResponse,
    CafeMapSummaryResponse,
    CafeSearchResponse,
    ContributorResponse,
    DataSourceManifestItem,
    EvidenceResponse,
    ExternalLinksResponse,
    HealthResponse,
    HotspotStatusResponse,
    LicenseLink,
    SourceManifestResponse,
    TrendPointResponse,
)


router = APIRouter(prefix="/api", tags=["map"])
VIEWPORT_TRUNCATED_HEADER = "X-BusyCafe-Viewport-Truncated"
_DIRECT_LINK_HOSTS = {
    "naver": {"map.naver.com", "m.map.naver.com", "m.place.naver.com"},
    "kakao": {"place.map.kakao.com"},
    "google": {"www.google.com", "maps.google.com", "google.com"},
}
_NAVER_MAP_DETAIL_PATH = re.compile(r"^/p/entry/place/([0-9]+)/?$")
_NAVER_MOBILE_DETAIL_PATH = re.compile(
    r"^/(?:place|restaurant)/([0-9]+)(?:/(?:home|menu|review|photo))?/?$"
)
_KAKAO_DETAIL_PATH = re.compile(r"^/([0-9]+)/?$")
_ORIGIN_PROVIDER_LABELS = {
    "overture": "Overture Places",
    "naver": "네이버 지도 등록 장소",
    "kakao": "카카오맵 등록 장소",
    "google": "Google Maps 등록 장소",
    "seoul_refreshment_permits": "서울시 영업 인허가 원장",
}
_SOURCE_MANIFEST = SourceManifestResponse(
    sources=[
        DataSourceManifestItem(
            id="seoul-citydata",
            role="crowd_observation",
            name="서울시 실시간 도시데이터",
            attribution=(
                "서울특별시 서울시 실시간 도시데이터(OA-21285), "
                "공공누리 제1유형"
            ),
            source_url=(
                "https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do"
            ),
            licenses=[
                LicenseLink(
                    name="공공누리 제1유형",
                    url="https://www.kogl.or.kr/info/licenseType1.do",
                )
            ],
        ),
        DataSourceManifestItem(
            id="overture-places",
            role="place_catalog",
            name="Overture Places",
            attribution="Overture Maps Foundation",
            source_url="https://docs.overturemaps.org/attribution/",
            release=OVERTURE_RELEASE,
            licenses=[
                LicenseLink(
                    name="CDLA-Permissive-2.0",
                    url="https://cdla.dev/permissive-2-0/",
                ),
                LicenseLink(
                    name="CC0-1.0",
                    url="https://creativecommons.org/publicdomain/zero/1.0/",
                ),
            ],
        ),
        DataSourceManifestItem(
            id="seoul-refreshment-permits",
            role="place_verification",
            name="서울시 휴게음식점 인허가 정보",
            attribution=(
                "서울특별시 서울시 휴게음식점 인허가 정보(OA-16095), "
                "공공누리 제1유형"
            ),
            source_url=(
                "https://data.seoul.go.kr/dataList/OA-16095/S/1/datasetView.do"
            ),
            licenses=[
                LicenseLink(
                    name="공공누리 제1유형",
                    url="https://www.kogl.or.kr/info/licenseType1.do",
                )
            ],
        ),
        DataSourceManifestItem(
            id="kakao-local",
            role="place_catalog_and_identity",
            name="카카오 로컬 API",
            attribution="카카오 로컬 API (카페 카테고리 CE7)",
            source_url=(
                "https://developers.kakao.com/docs/latest/ko/local/"
                "dev-guide#search-by-category"
            ),
            licenses=[
                LicenseLink(
                    name="Kakao API 운영정책",
                    url=(
                        "https://developers.kakao.com/terms/latest/ko/"
                        "site-policies"
                    ),
                )
            ],
        ),
        DataSourceManifestItem(
            id="openfreemap",
            role="basemap",
            name="OpenFreeMap",
            attribution="OpenFreeMap © OpenMapTiles Data from OpenStreetMap",
            source_url="https://openfreemap.org/",
            licenses=[
                LicenseLink(
                    name="OpenFreeMap Terms of Service",
                    url="https://openfreemap.org/tos/",
                ),
                LicenseLink(
                    name="OpenMapTiles BSD-3-Clause / CC-BY 4.0",
                    url=(
                        "https://github.com/openmaptiles/openmaptiles/"
                        "blob/master/LICENSE.md"
                    ),
                ),
                LicenseLink(
                    name="OpenStreetMap ODbL",
                    url="https://www.openstreetmap.org/copyright",
                ),
            ],
        ),
    ]
)


def _utc(value: datetime | None) -> datetime | None:
    """Restore SQLite's lost timezone metadata using the UTC storage contract."""

    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        parsed = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="bbox must contain four numbers") from error
    if len(parsed) != 4 or not all(isfinite(part) for part in parsed):
        raise HTTPException(status_code=422, detail="bbox must contain four finite numbers")
    min_lng, min_lat, max_lng, max_lat = parsed
    if not (-180 <= min_lng <= max_lng <= 180 and -90 <= min_lat <= max_lat <= 90):
        raise HTTPException(status_code=422, detail="bbox coordinates are invalid")
    if (
        max_lng - min_lng > MAX_BBOX_SPAN_DEG
        or max_lat - min_lat > MAX_BBOX_SPAN_DEG
    ):
        raise HTTPException(status_code=422, detail="bbox span is too large")
    return min_lng, min_lat, max_lng, max_lat


def _safe_external_links(value: object) -> ExternalLinksResponse:
    raw = value if isinstance(value, dict) else {}
    links: dict[str, str | None] = {}
    for provider, allowed_hosts in _DIRECT_LINK_HOSTS.items():
        candidate = raw.get(provider)
        if not isinstance(candidate, str):
            links[provider] = None
            continue
        parsed = urlparse(candidate)
        query = parse_qs(parsed.query)
        is_detail = {
            "naver": (
                bool(_NAVER_MAP_DETAIL_PATH.fullmatch(parsed.path))
                if parsed.hostname in {"map.naver.com", "m.map.naver.com"}
                else bool(_NAVER_MOBILE_DETAIL_PATH.fullmatch(parsed.path))
            ),
            "kakao": bool(_KAKAO_DETAIL_PATH.fullmatch(parsed.path)),
            "google": (
                "/maps/place/" in parsed.path
                or "query_place_id" in query
                or "cid" in query
            ),
        }[provider]
        links[provider] = candidate if (
            parsed.scheme == "https"
            and parsed.hostname in allowed_hosts
            and is_detail
        ) else None
    return ExternalLinksResponse(**links)


def _cafe_external_links(cafe: Cafe) -> ExternalLinksResponse:
    """Prefer active normalized provider rows; retain JSON during rollout."""

    fallback = _safe_external_links(cafe.external_links_json)
    resolved = fallback.model_dump()
    for place in cafe.provider_places:
        if place.provider not in _DIRECT_LINK_HOSTS:
            continue
        # A normalized identity is authoritative for its provider. Inactive or
        # invalid rows must suppress stale rollout JSON instead of reviving it.
        resolved[place.provider] = None
        if not place.active:
            continue
        validated = _safe_external_links({place.provider: place.detail_url})
        candidate = getattr(validated, place.provider)
        if (
            candidate is not None
            and _provider_place_id_from_detail_url(place.provider, candidate)
            == place.provider_place_id
        ):
            resolved[place.provider] = candidate
    resolved["naver_search"] = (
        _naver_map_search_url(cafe) if resolved["naver"] is None else None
    )
    return ExternalLinksResponse(**resolved)


def _naver_map_search_url(cafe: Cafe) -> str | None:
    """Build an address-first map search without claiming a provider identity."""

    name = " ".join(cafe.name.split())
    road_address = " ".join((cafe.road_address or "").split())
    if not name or not road_address:
        return None
    query = quote(f"{road_address} {name}", safe="")
    return f"{NAVER_MAP_SEARCH_BASE_URL}/{query}"


def _provider_place_id_from_detail_url(
    provider: str, candidate: str
) -> str | None:
    """Extract an exact identity from an already allow-listed detail URL."""

    parsed = urlparse(candidate)
    if provider == "naver":
        matched = (
            _NAVER_MAP_DETAIL_PATH.fullmatch(parsed.path)
            if parsed.hostname in {"map.naver.com", "m.map.naver.com"}
            else _NAVER_MOBILE_DETAIL_PATH.fullmatch(parsed.path)
        )
        return matched.group(1) if matched else None
    if provider == "kakao":
        matched = _KAKAO_DETAIL_PATH.fullmatch(parsed.path)
        return matched.group(1) if matched else None
    if provider == "google":
        query = parse_qs(parsed.query)
        place_ids = query.get("query_place_id") or query.get("cid")
        if place_ids and len(place_ids) == 1 and place_ids[0].strip():
            return place_ids[0].strip()
    return None


def _safe_website(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.hostname else None


def _cafe_source_label(cafe: Cafe) -> str:
    permit_verified = isinstance(cafe.source_json, list) and any(
        isinstance(source, dict) and source.get("dataset_id") == "OA-16095"
        for source in cafe.source_json
    )
    origin_label = _ORIGIN_PROVIDER_LABELS.get(
        cafe.origin_provider, cafe.origin_provider
    )
    label = (
        f"{origin_label} · {cafe.source_release} · "
        f"장소 원장 품질 {cafe.source_confidence:.2f}"
    )
    if permit_verified:
        label += " · 서울시 영업 인허가 대조"
    if os.getenv("CAFE_CROWD_SNAPSHOT") == "1":
        label += " · 배포 스냅샷"
    return label


def _observation_freshness(
    observed_at: datetime | None,
    *,
    now: datetime,
) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    normalized = _utc(observed_at)
    if normalized is None:
        return "stale"
    age_min = (now - normalized).total_seconds() / 60.0
    if (
        age_min > CURRENT_DISPLAY_MAX_AGE_MIN
        or age_min < -FRESHNESS_MAX_FUTURE_SKEW_MIN
    ):
        return "stale"
    return "delayed" if age_min > STALE_WARN_MIN else "fresh"


def _observation_age_minutes(
    observed_at: datetime | None,
    *,
    now: datetime,
) -> int | None:
    """Return whole observation age without understating a partial minute."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    normalized = _utc(observed_at)
    if normalized is None:
        return None
    age_min = (now - normalized).total_seconds() / 60.0
    if age_min < -FRESHNESS_MAX_FUTURE_SKEW_MIN:
        return None
    return max(0, ceil(age_min))


def _cafe_map_response(
    cafe: Cafe,
    score: CafeScore | None,
    hotspot: Hotspot | None,
    *,
    now: datetime | None = None,
) -> CafeMapResponse:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    normalized_observed_at = _utc(score.source_observed_at) if score else None
    freshness = (
        "n/a"
        if score is None
        else _observation_freshness(
            normalized_observed_at,
            now=current_time,
        )
    )
    expose_level = score is not None and freshness in ("fresh", "delayed")
    expose_confidence = score is not None and freshness == "fresh"
    return CafeMapResponse(
        id=cafe.id,
        name=cafe.name,
        lat=cafe.lat,
        lng=cafe.lng,
        level=score.level if expose_level else None,
        confidence=score.confidence if expose_confidence else None,
        freshness=freshness,
        coverage=(score.coverage if score else "uncovered"),
        evidence=EvidenceResponse(
            hotspot_name=hotspot.name if hotspot else None,
            distance_m=score.primary_distance_m if score else None,
            observed_at=normalized_observed_at,
            age_minutes=(
                _observation_age_minutes(normalized_observed_at, now=current_time)
                if score is not None
                else None
            ),
        ),
    )


def _cafe_map_summary_response(
    cafe: Cafe,
    score: CafeScore | None,
    *,
    now: datetime | None = None,
) -> CafeMapSummaryResponse:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    normalized_observed_at = _utc(score.source_observed_at) if score else None
    freshness = (
        "n/a"
        if score is None
        else _observation_freshness(normalized_observed_at, now=current_time)
    )
    expose_level = score is not None and freshness in ("fresh", "delayed")
    expose_confidence = score is not None and freshness == "fresh"
    return CafeMapSummaryResponse(
        id=cafe.id,
        name=cafe.name,
        lat=cafe.lat,
        lng=cafe.lng,
        level=score.level if expose_level else None,
        confidence=score.confidence if expose_confidence else None,
        freshness=freshness,
        coverage=(score.coverage if score else "uncovered"),
        age_minutes=(
            _observation_age_minutes(normalized_observed_at, now=current_time)
            if score is not None
            else None
        ),
    )


def _normalized_search_term(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).lower()
    return normalized or None


def _brand_search_terms(value: str | None) -> tuple[str, ...] | None:
    normalized = _normalized_search_term(value)
    if normalized is None:
        return None
    for canonical, aliases in CAFE_SEARCH_BRAND_ALIASES.items():
        normalized_aliases = tuple(
            term
            for alias in aliases
            if (term := _normalized_search_term(alias)) is not None
        )
        if normalized in {
            _normalized_search_term(canonical),
            *normalized_aliases,
        }:
            return (
                normalized,
                *(term for term in normalized_aliases if term != normalized),
            )
    return ()


def _cafe_search_response(
    cafe: Cafe,
    score: CafeScore | None,
    hotspot: Hotspot | None,
    *,
    now: datetime,
) -> CafeSearchResponse:
    base = _cafe_map_response(cafe, score, hotspot, now=now)
    return CafeSearchResponse(
        **base.model_dump(),
        road_address=cafe.road_address,
    )


@router.get("/sources", response_model=SourceManifestResponse)
def sources() -> SourceManifestResponse:
    return _SOURCE_MANIFEST


@router.get("/cafes", response_model=list[CafeMapResponse])
def list_cafes(
    response: Response,
    bbox: str = Query(..., description="minLng,minLat,maxLng,maxLat"),
    min_conf: float = Query(0, ge=0, le=1),
    db: Session = Depends(get_db),
) -> list[CafeMapResponse]:
    min_lng, min_lat, max_lng, max_lat = _parse_bbox(bbox)
    statement = (
        select(Cafe, CafeScore, Hotspot)
        .options(
            load_only(Cafe.id, Cafe.name, Cafe.lat, Cafe.lng, raiseload=True),
            load_only(
                CafeScore.cafe_id,
                CafeScore.source_observed_at,
                CafeScore.level,
                CafeScore.confidence,
                CafeScore.coverage,
                CafeScore.primary_hotspot_id,
                CafeScore.primary_distance_m,
                raiseload=True,
            ),
            load_only(Hotspot.id, Hotspot.name, raiseload=True),
            raiseload(Cafe.provider_places),
        )
        .outerjoin(CafeScore, CafeScore.cafe_id == Cafe.id)
        .outerjoin(Hotspot, Hotspot.id == CafeScore.primary_hotspot_id)
        .where(
            Cafe.active.is_(True),
            Cafe.lng.between(min_lng, max_lng),
            Cafe.lat.between(min_lat, max_lat),
        )
        .order_by(Cafe.id)
        .limit(MAX_CAFES_PER_VIEWPORT + 1)
    )
    if min_conf > 0:
        statement = statement.where(CafeScore.confidence >= min_conf)
    rows = db.execute(statement).all()
    truncated = len(rows) > MAX_CAFES_PER_VIEWPORT
    response.headers[VIEWPORT_TRUNCATED_HEADER] = str(truncated).lower()
    if truncated:
        return []
    request_time = datetime.now(UTC)
    items = [
        _cafe_map_response(cafe, score, hotspot, now=request_time)
        for cafe, score, hotspot in rows
    ]
    return [
        item
        for item in items
        if min_conf == 0
        or (item.confidence is not None and item.confidence >= min_conf)
    ]


@router.get("/cafes/summary", response_model=list[CafeMapSummaryResponse])
def list_cafe_summaries(
    response: Response,
    bbox: str = Query(..., description="minLng,minLat,maxLng,maxLat"),
    min_conf: float = Query(0, ge=0, le=1),
    db: Session = Depends(get_db),
) -> list[CafeMapSummaryResponse]:
    min_lng, min_lat, max_lng, max_lat = _parse_bbox(bbox)
    statement = (
        select(Cafe, CafeScore)
        .options(
            load_only(Cafe.id, Cafe.name, Cafe.lat, Cafe.lng, raiseload=True),
            load_only(
                CafeScore.cafe_id,
                CafeScore.source_observed_at,
                CafeScore.level,
                CafeScore.confidence,
                CafeScore.coverage,
                raiseload=True,
            ),
            raiseload(Cafe.provider_places),
        )
        .outerjoin(CafeScore, CafeScore.cafe_id == Cafe.id)
        .where(
            Cafe.active.is_(True),
            Cafe.lng.between(min_lng, max_lng),
            Cafe.lat.between(min_lat, max_lat),
        )
        .order_by(Cafe.id)
        .limit(MAX_CAFES_PER_VIEWPORT + 1)
    )
    if min_conf > 0:
        statement = statement.where(CafeScore.confidence >= min_conf)
    rows = db.execute(statement).all()
    truncated = len(rows) > MAX_CAFES_PER_VIEWPORT
    response.headers[VIEWPORT_TRUNCATED_HEADER] = str(truncated).lower()
    if truncated:
        return []
    request_time = datetime.now(UTC)
    items = [
        _cafe_map_summary_response(cafe, score, now=request_time)
        for cafe, score in rows
    ]
    return [
        item
        for item in items
        if min_conf == 0
        or (item.confidence is not None and item.confidence >= min_conf)
    ]


@router.get("/cafes/search", response_model=list[CafeSearchResponse])
def search_cafes(
    q: str | None = Query(
        default=None,
        min_length=CAFE_SEARCH_MIN_QUERY_LENGTH,
        max_length=CAFE_SEARCH_MAX_QUERY_LENGTH,
    ),
    brand: str | None = Query(
        default=None,
        min_length=CAFE_SEARCH_MIN_QUERY_LENGTH,
        max_length=CAFE_SEARCH_MAX_QUERY_LENGTH,
    ),
    limit: int = Query(
        CAFE_SEARCH_DEFAULT_LIMIT,
        ge=1,
        le=CAFE_SEARCH_MAX_LIMIT,
    ),
    db: Session = Depends(get_db),
) -> list[CafeSearchResponse]:
    """Search only the materialized Seoul cafe catalog; never call providers."""

    normalized_q = _normalized_search_term(q)
    if (
        normalized_q is not None
        and len(normalized_q) < CAFE_SEARCH_MIN_QUERY_LENGTH
    ):
        raise HTTPException(status_code=422, detail="q is too short")
    brand_terms = _brand_search_terms(brand)
    if brand_terms == ():
        raise HTTPException(status_code=422, detail="unsupported brand")
    if normalized_q is None and brand_terms is None:
        raise HTTPException(
            status_code=422,
            detail="q or brand must contain a search term",
        )

    min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
    statement = (
        select(Cafe, CafeScore, Hotspot)
        .options(
            load_only(
                Cafe.id,
                Cafe.name,
                Cafe.lat,
                Cafe.lng,
                Cafe.road_address,
                raiseload=True,
            ),
            load_only(
                CafeScore.cafe_id,
                CafeScore.source_observed_at,
                CafeScore.level,
                CafeScore.confidence,
                CafeScore.coverage,
                CafeScore.primary_hotspot_id,
                CafeScore.primary_distance_m,
                raiseload=True,
            ),
            load_only(Hotspot.id, Hotspot.name, raiseload=True),
            raiseload(Cafe.provider_places),
        )
        .outerjoin(CafeScore, CafeScore.cafe_id == Cafe.id)
        .outerjoin(Hotspot, Hotspot.id == CafeScore.primary_hotspot_id)
        .where(
            Cafe.active.is_(True),
            Cafe.lng.between(min_lng, max_lng),
            Cafe.lat.between(min_lat, max_lat),
        )
    )
    lower_name = func.lower(Cafe.name)
    if normalized_q is not None:
        statement = statement.where(
            or_(
                lower_name.contains(normalized_q, autoescape=True),
                func.lower(Cafe.road_address).contains(
                    normalized_q,
                    autoescape=True,
                ),
            )
        )
    if brand_terms is not None:
        statement = statement.where(
            or_(
                *(
                    lower_name.contains(term, autoescape=True)
                    for term in brand_terms
                )
            )
        )

    relevance_term = normalized_q or brand_terms[0]
    statement = statement.order_by(
        case(
            (lower_name == relevance_term, 0),
            (lower_name.startswith(relevance_term, autoescape=True), 1),
            (lower_name.contains(relevance_term, autoescape=True), 2),
            else_=3,
        ),
        lower_name,
        Cafe.id,
    ).limit(limit)

    request_time = datetime.now(UTC)
    return [
        _cafe_search_response(cafe, score, hotspot, now=request_time)
        for cafe, score, hotspot in db.execute(statement).all()
    ]


@router.get("/cafes/{cafe_id}", response_model=CafeDetailResponse)
def get_cafe(
    cafe_id: int,
    db: Session = Depends(get_db),
) -> CafeDetailResponse:
    row = db.execute(
        select(Cafe, CafeScore, Hotspot, HotspotServingState)
        .options(selectinload(Cafe.provider_places))
        .outerjoin(CafeScore, CafeScore.cafe_id == Cafe.id)
        .outerjoin(Hotspot, Hotspot.id == CafeScore.primary_hotspot_id)
        .outerjoin(
            HotspotServingState,
            HotspotServingState.hotspot_id == CafeScore.primary_hotspot_id,
        )
        .where(Cafe.id == cafe_id, Cafe.active.is_(True))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="cafe not found")
    cafe, score, hotspot, serving_state = row
    base = _cafe_map_response(
        cafe,
        score,
        hotspot,
        now=datetime.now(UTC),
    )
    trend: list[TrendPointResponse] = []
    state_matches_score = (
        score is not None
        and serving_state is not None
        and _utc(serving_state.observed_at) == _utc(score.source_observed_at)
    )
    if state_matches_score and serving_state.trend_12h_json:
        trend = [
            TrendPointResponse.model_validate(item)
            for item in serving_state.trend_12h_json
        ]
    contributors = []
    if score and score.contributors_json:
        contributors = [ContributorResponse.model_validate(item) for item in score.contributors_json]
    forecast_1h = None
    if (
        base.freshness == "fresh"
        and state_matches_score
        and serving_state.forecast_1h_json
    ):
        forecast_1h = serving_state.forecast_1h_json
    return CafeDetailResponse(
        **base.model_dump(),
        road_address=cafe.road_address,
        phone=cafe.phone,
        website=_safe_website(cafe.website),
        source_label=_cafe_source_label(cafe),
        model_version=score.model_version if score else None,
        score=(score.score if score and base.level is not None else None),
        confidence_tier=(
            score.confidence_tier if score and base.confidence is not None else None
        ),
        external_links=_cafe_external_links(cafe),
        primary_hotspot_id=score.primary_hotspot_id if score else None,
        contributors=contributors,
        trend_12h=trend,
        forecast_1h=forecast_1h,
    )


@router.get("/hotspots", response_model=list[HotspotStatusResponse])
def list_hotspots(db: Session = Depends(get_db)) -> list[HotspotStatusResponse]:
    latest = (
        select(
            HotspotSnapshot.hotspot_id,
            func.max(HotspotSnapshot.observed_at).label("observed_at"),
        )
        .group_by(HotspotSnapshot.hotspot_id)
        .subquery()
    )
    rows = db.execute(
        select(Hotspot, HotspotSnapshot)
        .outerjoin(latest, latest.c.hotspot_id == Hotspot.id)
        .outerjoin(
            HotspotSnapshot,
            and_(
                HotspotSnapshot.hotspot_id == latest.c.hotspot_id,
                HotspotSnapshot.observed_at == latest.c.observed_at,
            ),
        )
        .where(Hotspot.is_polled.is_(True))
        .order_by(Hotspot.area_cd)
    ).all()
    now = datetime.now(UTC)
    responses: list[HotspotStatusResponse] = []
    for hotspot, snapshot in rows:
        observed_at = _utc(snapshot.observed_at) if snapshot else None
        freshness = (
            _observation_freshness(observed_at, now=now)
            if snapshot
            else "n/a"
        )
        responses.append(
            HotspotStatusResponse(
                id=hotspot.id,
                area_cd=hotspot.area_cd,
                name=hotspot.name,
                lat=hotspot.lat,
                lng=hotspot.lng,
                observed_at=observed_at,
                level=(
                    snapshot.congest_level
                    if snapshot and freshness in ("fresh", "delayed")
                    else None
                ),
                freshness=freshness,
            )
        )
    return responses


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    now = datetime.now(UTC)
    latest_cycle = (
        select(
            IngestCycle.status,
            IngestCycle.targets,
            IngestCycle.saved,
            IngestCycle.failed,
        )
        .order_by(
            IngestCycle.started_at.desc(), IngestCycle.id.desc()
        )
        .limit(1)
        .cte("latest_cycle")
    )
    stats = db.execute(
        select(
            select(func.max(HotspotSnapshot.fetched_at))
            .scalar_subquery()
            .label("last_ingest_at"),
            select(func.max(IngestCycle.completed_at))
            .where(IngestCycle.status == "complete")
            .scalar_subquery()
            .label("last_complete_cycle_at"),
            select(latest_cycle.c.status)
            .scalar_subquery()
            .label("last_cycle_status"),
            select(latest_cycle.c.targets)
            .scalar_subquery()
            .label("last_cycle_targets"),
            select(latest_cycle.c.saved)
            .scalar_subquery()
            .label("last_cycle_saved"),
            select(latest_cycle.c.failed)
            .scalar_subquery()
            .label("last_cycle_failed"),
            select(func.count())
            .select_from(HotspotSnapshot)
            .where(HotspotSnapshot.fetched_at >= now - timedelta(hours=1))
            .scalar_subquery()
            .label("snapshots_last_hour"),
            select(func.count())
            .select_from(Cafe)
            .where(Cafe.active.is_(True))
            .scalar_subquery()
            .label("cafes_count"),
        )
    ).one()
    return HealthResponse(
        data_mode=(
            "snapshot" if os.getenv("CAFE_CROWD_SNAPSHOT") == "1" else "live"
        ),
        stale_warn_min=STALE_WARN_MIN,
        current_display_max_age_min=CURRENT_DISPLAY_MAX_AGE_MIN,
        last_ingest_at=_utc(stats.last_ingest_at),
        last_complete_cycle_at=_utc(stats.last_complete_cycle_at),
        last_cycle_status=stats.last_cycle_status,
        last_cycle_targets=stats.last_cycle_targets,
        last_cycle_saved=stats.last_cycle_saved,
        last_cycle_failed=stats.last_cycle_failed,
        snapshots_last_hour=stats.snapshots_last_hour or 0,
        cafes_count=stats.cafes_count or 0,
    )
