"""Read-only map API backed exclusively by the local PostgreSQL cache."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import MAX_CAFES_PER_VIEWPORT
from app.database import get_db
from app.models import Cafe, CafeScore, Hotspot, HotspotSnapshot
from app.schemas import (
    CafeDetailResponse,
    CafeMapResponse,
    ContributorResponse,
    EvidenceResponse,
    ExternalLinksResponse,
    HealthResponse,
    HotspotStatusResponse,
    TrendPointResponse,
)


router = APIRouter(prefix="/api", tags=["map"])
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
_DIRECT_LINK_HOSTS = {
    "naver": {"map.naver.com", "m.map.naver.com"},
    "kakao": {"place.map.kakao.com"},
    "google": {"www.google.com", "maps.google.com", "google.com"},
}


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


def _cafe_response(
    cafe: Cafe,
    score: CafeScore | None,
    hotspot: Hotspot | None,
    observed_at: datetime | None = None,
) -> CafeMapResponse:
    return CafeMapResponse(
        id=cafe.id,
        name=cafe.name,
        lat=cafe.lat,
        lng=cafe.lng,
        road_address=cafe.road_address,
        phone=cafe.phone,
        website=_safe_website(cafe.website),
        source_label=(
            f"Overture Places · {cafe.source_release} · 신뢰도 {cafe.source_confidence:.2f}"
            + (" · 배포 스냅샷" if os.getenv("CAFE_CROWD_SNAPSHOT") == "1" else "")
        ),
        level=score.level if score else None,
        score=score.score if score else None,
        confidence=score.confidence if score else None,
        confidence_tier=score.confidence_tier if score else None,
        coverage=(score.coverage if score else "uncovered"),
        evidence=EvidenceResponse(
            hotspot_name=hotspot.name if hotspot else None,
            distance_m=score.primary_distance_m if score else None,
            observed_at=_utc(observed_at),
        ),
        external_links=_safe_external_links(cafe.external_links_json),
    )


def _set_cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=60"


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
    return [
        _cafe_response(cafe, score, hotspot, observed_at)
        for cafe, score, hotspot, observed_at in rows
    ]


@router.get("/cafes/{cafe_id}", response_model=CafeDetailResponse)
def get_cafe(cafe_id: int, response: Response, db: Session = Depends(get_db)) -> CafeDetailResponse:
    row = db.execute(
        select(Cafe, CafeScore, Hotspot)
        .outerjoin(CafeScore, CafeScore.cafe_id == Cafe.id)
        .outerjoin(Hotspot, Hotspot.id == CafeScore.primary_hotspot_id)
        .where(Cafe.id == cafe_id, Cafe.active.is_(True))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="cafe not found")
    cafe, score, hotspot = row
    base = _cafe_response(cafe, score, hotspot)
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
    if latest_snapshot and latest_snapshot.forecast_json:
        target = latest_snapshot.observed_at + timedelta(hours=1)
        def forecast_distance(item: dict[str, Any]) -> float:
            try:
                candidate = datetime.strptime(
                    item["FCST_TIME"], "%Y-%m-%d %H:%M"
                ).replace(tzinfo=SEOUL_TIMEZONE).astimezone(UTC)
                return abs((candidate - target).total_seconds())
            except (KeyError, TypeError, ValueError):
                return float("inf")
        forecast_1h = min(latest_snapshot.forecast_json, key=forecast_distance, default=None)
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
    return [
        HotspotStatusResponse(
            id=hotspot.id,
            area_cd=hotspot.area_cd,
            name=hotspot.name,
            lat=hotspot.lat,
            lng=hotspot.lng,
            observed_at=_utc(snapshot.observed_at) if snapshot else None,
            level=snapshot.congest_level if snapshot else None,
        )
        for hotspot, snapshot in rows
    ]


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    now = datetime.now(UTC)
    return HealthResponse(
        last_ingest_at=_utc(db.scalar(select(func.max(HotspotSnapshot.fetched_at)))),
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
