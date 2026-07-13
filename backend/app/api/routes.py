"""Read-only map API backed exclusively by the local PostgreSQL cache."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from math import ceil, isfinite
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import (
    CURRENT_DISPLAY_MAX_AGE_MIN,
    FRESHNESS_MAX_FUTURE_SKEW_MIN,
    MAX_CAFES_PER_VIEWPORT,
    OVERTURE_RELEASE,
    STALE_WARN_MIN,
)
from app.database import get_db
from app.models import Cafe, CafeScore, Hotspot, HotspotSnapshot, IngestCycle
from app.schemas import (
    CafeDetailResponse,
    CafeMapResponse,
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
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
_DIRECT_LINK_HOSTS = {
    "naver": {"map.naver.com", "m.map.naver.com"},
    "kakao": {"place.map.kakao.com"},
    "google": {"www.google.com", "maps.google.com", "google.com"},
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
            "naver": "/place/" in parsed.path,
            "kakao": parsed.path.strip("/").isdigit(),
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


def _safe_website(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.hostname else None


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


def _cafe_response(
    cafe: Cafe,
    score: CafeScore | None,
    hotspot: Hotspot | None,
    observed_at: datetime | None = None,
    *,
    now: datetime | None = None,
) -> CafeMapResponse:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    normalized_observed_at = _utc(observed_at)
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
        road_address=cafe.road_address,
        phone=cafe.phone,
        website=_safe_website(cafe.website),
        source_label=(
            f"Overture Places · {cafe.source_release} · 장소 원장 품질 {cafe.source_confidence:.2f}"
            + (" · 배포 스냅샷" if os.getenv("CAFE_CROWD_SNAPSHOT") == "1" else "")
        ),
        model_version=score.model_version if score else None,
        level=score.level if expose_level else None,
        score=score.score if expose_level else None,
        confidence=score.confidence if expose_confidence else None,
        confidence_tier=score.confidence_tier if expose_confidence else None,
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
        external_links=_safe_external_links(cafe.external_links_json),
    )


def _set_cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=60"


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
    latest_observed_at = (
        select(func.max(HotspotSnapshot.observed_at))
        .where(HotspotSnapshot.hotspot_id == CafeScore.primary_hotspot_id)
        .correlate(CafeScore)
        .scalar_subquery()
    )
    statement = (
        select(Cafe, CafeScore, Hotspot, latest_observed_at)
        .outerjoin(CafeScore, CafeScore.cafe_id == Cafe.id)
        .outerjoin(Hotspot, Hotspot.id == CafeScore.primary_hotspot_id)
        .where(
            Cafe.active.is_(True),
            Cafe.lng.between(min_lng, max_lng),
            Cafe.lat.between(min_lat, max_lat),
        )
        .order_by(Cafe.id)
        .limit(MAX_CAFES_PER_VIEWPORT)
    )
    if min_conf > 0:
        statement = statement.where(CafeScore.confidence >= min_conf)
    rows = db.execute(statement).all()
    _set_cache_headers(response)
    request_time = datetime.now(UTC)
    items = [
        _cafe_response(cafe, score, hotspot, observed_at, now=request_time)
        for cafe, score, hotspot, observed_at in rows
    ]
    return [
        item
        for item in items
        if min_conf == 0
        or (item.confidence is not None and item.confidence >= min_conf)
    ]


@router.get("/cafes/{cafe_id}", response_model=CafeDetailResponse)
def get_cafe(
    cafe_id: int,
    response: Response,
    db: Session = Depends(get_db),
) -> CafeDetailResponse:
    latest_observed_at = (
        select(func.max(HotspotSnapshot.observed_at))
        .where(HotspotSnapshot.hotspot_id == CafeScore.primary_hotspot_id)
        .correlate(CafeScore)
        .scalar_subquery()
    )
    row = db.execute(
        select(Cafe, CafeScore, Hotspot, latest_observed_at)
        .outerjoin(CafeScore, CafeScore.cafe_id == Cafe.id)
        .outerjoin(Hotspot, Hotspot.id == CafeScore.primary_hotspot_id)
        .where(Cafe.id == cafe_id, Cafe.active.is_(True))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="cafe not found")
    cafe, score, hotspot, observed_at = row
    base = _cafe_response(
        cafe,
        score,
        hotspot,
        observed_at,
        now=datetime.now(UTC),
    )
    latest_snapshot: HotspotSnapshot | None = None
    trend: list[TrendPointResponse] = []
    if score and score.primary_hotspot_id:
        since = datetime.now(UTC) - timedelta(hours=12)
        snapshots = db.scalars(
            select(HotspotSnapshot)
            .where(
                HotspotSnapshot.hotspot_id == score.primary_hotspot_id,
                HotspotSnapshot.observed_at >= since,
            )
            .order_by(HotspotSnapshot.observed_at)
        ).all()
        trend = [
            TrendPointResponse(observed_at=_utc(item.observed_at), level=item.congest_level)
            for item in snapshots
        ]
        if snapshots:
            latest_snapshot = snapshots[-1]
            base.evidence.observed_at = _utc(latest_snapshot.observed_at)
    contributors = []
    if score and score.contributors_json:
        contributors = [ContributorResponse.model_validate(item) for item in score.contributors_json]
    forecast_1h = None
    if (
        base.freshness == "fresh"
        and latest_snapshot
        and latest_snapshot.forecast_json
    ):
        target = latest_snapshot.observed_at + timedelta(hours=1)
        def forecast_distance(item: dict[str, Any]) -> float:
            try:
                candidate = datetime.strptime(
                    item["FCST_TIME"], "%Y-%m-%d %H:%M"
                ).replace(tzinfo=SEOUL_TIMEZONE).astimezone(UTC)
                return abs((candidate - target).total_seconds())
            except (KeyError, TypeError, ValueError):
                return float("inf")
        forecast_1h = min(
            latest_snapshot.forecast_json,
            key=forecast_distance,
            default=None,
        )
    _set_cache_headers(response)
    return CafeDetailResponse(
        **base.model_dump(),
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
    latest_cycle = db.scalar(
        select(IngestCycle).order_by(
            IngestCycle.started_at.desc(), IngestCycle.id.desc()
        ).limit(1)
    )
    return HealthResponse(
        data_mode=(
            "snapshot" if os.getenv("CAFE_CROWD_SNAPSHOT") == "1" else "live"
        ),
        stale_warn_min=STALE_WARN_MIN,
        current_display_max_age_min=CURRENT_DISPLAY_MAX_AGE_MIN,
        last_ingest_at=_utc(db.scalar(select(func.max(HotspotSnapshot.fetched_at)))),
        last_complete_cycle_at=_utc(
            db.scalar(
                select(func.max(IngestCycle.completed_at)).where(
                    IngestCycle.status == "complete"
                )
            )
        ),
        last_cycle_status=latest_cycle.status if latest_cycle else None,
        last_cycle_targets=latest_cycle.targets if latest_cycle else None,
        last_cycle_saved=latest_cycle.saved if latest_cycle else None,
        last_cycle_failed=latest_cycle.failed if latest_cycle else None,
        snapshots_last_hour=db.scalar(
            select(func.count())
            .select_from(HotspotSnapshot)
            .where(HotspotSnapshot.fetched_at >= now - timedelta(hours=1))
        )
        or 0,
        cafes_count=db.scalar(
            select(func.count()).select_from(Cafe).where(Cafe.active.is_(True))
        )
        or 0,
    )
