from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import _observation_freshness
from app.config import (
    FRESHNESS_MAX_FUTURE_SKEW_MIN,
    OVERTURE_RELEASE,
    SCORING_MODEL_VERSION,
)
from app.database import get_db
from app.main import create_app
from app.models import Base, Cafe, CafeScore, Hotspot, HotspotSnapshot, IngestCycle


@pytest.fixture
def api_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with factory() as session:
        hotspot = Hotspot(
            area_cd="POI001",
            name="테스트 핫스팟",
            category="test",
            lat=37.55,
            lng=126.98,
            is_polled=True,
        )
        cafe = Cafe(
            overture_id="overture:test-1",
            source_release="2026-06-17.0",
            source_confidence=0.95,
            primary_category="cafe",
            name="정확한 카페",
            lat=37.551,
            lng=126.981,
            road_address="서울시 테스트구 1",
            phone="02-123-4567",
            website="https://example.test",
            external_links_json={
                "naver": "https://map.naver.com/p/entry/place/123",
                "kakao": "https://place.map.kakao.com/456",
                "google": "https://www.google.com/maps/place/?q=place_id:test",
            },
        )
        inactive = Cafe(
            overture_id="overture:inactive",
            source_release="2026-06-17.0",
            source_confidence=0.9,
            primary_category="cafe",
            name="비활성 카페",
            lat=37.552,
            lng=126.982,
            active=False,
        )
        session.add_all([hotspot, cafe, inactive])
        session.flush()
        session.add(
            HotspotSnapshot(
                hotspot_id=hotspot.id,
                observed_at=now,
                fetched_at=now,
                congest_level=2,
                congest_label="보통",
                forecast_json=[],
            )
        )
        session.add(
            CafeScore(
                cafe_id=cafe.id,
                model_version=SCORING_MODEL_VERSION,
                computed_at=now,
                score=2.0,
                level=2,
                confidence=0.7,
                confidence_tier="high",
                coverage="covered",
                primary_hotspot_id=hotspot.id,
                primary_distance_m=120,
                contributors_json=[
                    {
                        "hotspot_id": hotspot.id,
                        "distance_m": 120,
                        "level": 2,
                        "weight": 1.0,
                    }
                ],
            )
        )
        session.add(
            IngestCycle(
                started_at=now - timedelta(minutes=1),
                completed_at=now - timedelta(minutes=1),
                targets=1,
                saved=1,
                failed=0,
                status="complete",
            )
        )
        session.add(
            IngestCycle(
                started_at=now,
                targets=1,
                saved=0,
                failed=0,
                status="running",
            )
        )
        session.commit()

    app = create_app()
    app.state.test_session_factory = factory

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_bbox_api_returns_active_cached_cafe_with_evidence(api_client) -> None:
    response = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    )

    assert response.status_code == 200
    assert "max-age=30" in response.headers["cache-control"]
    payload = response.json()
    assert [item["name"] for item in payload] == ["정확한 카페"]
    assert payload[0]["coverage"] == "covered"
    assert payload[0]["freshness"] == "fresh"
    assert payload[0]["model_version"] == SCORING_MODEL_VERSION
    assert payload[0]["license_manifest_url"] == "/api/sources"
    assert payload[0]["evidence"]["hotspot_name"] == "테스트 핫스팟"
    assert payload[0]["evidence"]["observed_at"] is not None
    assert payload[0]["evidence"]["observed_at"].endswith("Z")
    assert payload[0]["phone"] == "02-123-4567"
    assert payload[0]["website"] == "https://example.test"
    assert payload[0]["external_links"]["kakao"].endswith("/456")


def test_detail_api_returns_scoring_model_version(api_client) -> None:
    listing = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    ).json()

    response = api_client.get(f"/api/cafes/{listing[0]['id']}")

    assert response.status_code == 200
    assert response.json()["model_version"] == SCORING_MODEL_VERSION
    assert response.json()["freshness"] == "fresh"


def test_stale_snapshot_preserves_evidence_but_hides_current_score(api_client) -> None:
    stale_time = datetime.now(UTC) - timedelta(minutes=26)
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        snapshot = session.query(HotspotSnapshot).one()
        snapshot.observed_at = stale_time
        snapshot.fetched_at = stale_time
        session.commit()

    listing = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    ).json()

    assert len(listing) == 1
    item = listing[0]
    assert item["freshness"] == "stale"
    assert item["level"] is None
    assert item["score"] is None
    assert item["confidence"] is None
    assert item["confidence_tier"] is None
    assert item["coverage"] == "covered"
    assert item["model_version"] == SCORING_MODEL_VERSION
    assert item["evidence"]["hotspot_name"] == "테스트 핫스팟"
    assert item["evidence"]["observed_at"] is not None
    assert api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7", "min_conf": 0.1},
    ).json() == []

    detail = api_client.get(f"/api/cafes/{item['id']}").json()
    assert detail["freshness"] == "stale"
    assert detail["level"] is None
    assert detail["score"] is None
    assert detail["forecast_1h"] is None

    hotspot = api_client.get("/api/hotspots").json()[0]
    assert hotspot["freshness"] == "stale"
    assert hotspot["level"] is None
    assert hotspot["observed_at"] is not None


def test_observation_freshness_boundary_and_future_skew() -> None:
    now = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)

    assert _observation_freshness(
        now - timedelta(minutes=25), now=now
    ) == "fresh"
    assert _observation_freshness(
        now - timedelta(minutes=25, microseconds=1), now=now
    ) == "stale"
    assert _observation_freshness(None, now=now) == "stale"
    assert _observation_freshness(
        now + timedelta(minutes=FRESHNESS_MAX_FUTURE_SKEW_MIN), now=now
    ) == "fresh"
    assert _observation_freshness(
        now
        + timedelta(
            minutes=FRESHNESS_MAX_FUTURE_SKEW_MIN,
            microseconds=1,
        ),
        now=now,
    ) == "stale"


@pytest.mark.parametrize(
    "bbox",
    ["bad", "126,37,127", "127,37,126,38", "nan,37,127,38"],
)
def test_bbox_validation_fails_closed(api_client, bbox: str) -> None:
    response = api_client.get("/api/cafes", params={"bbox": bbox})
    assert response.status_code == 422


def test_search_or_untrusted_external_links_are_not_exposed(api_client) -> None:
    from app.api.routes import _safe_external_links

    links = _safe_external_links(
        {
            "naver": "https://map.naver.com/p/search/not-a-detail",
            "kakao": "https://map.kakao.com/link/search/not-a-detail",
            "google": "https://evil.example/maps/place/1",
        }
    )
    assert links.naver is None
    assert links.kakao is None
    assert links.google is None


def test_health_counts_only_active_cafes(api_client, monkeypatch) -> None:
    monkeypatch.delenv("CAFE_CROWD_SNAPSHOT", raising=False)

    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "live"
    assert response.json()["stale_warn_min"] == 25
    assert response.json()["cafes_count"] == 1
    assert response.json()["snapshots_last_hour"] == 1
    assert response.json()["last_complete_cycle_at"].endswith("Z")
    assert response.json()["last_cycle_status"] == "running"
    assert response.json()["last_cycle_targets"] == 1
    assert response.json()["last_cycle_saved"] == 0
    assert response.json()["last_cycle_failed"] == 0


def test_health_reports_snapshot_mode_from_runtime_environment(
    api_client, monkeypatch
) -> None:
    monkeypatch.setenv("CAFE_CROWD_SNAPSHOT", "1")

    response = api_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["data_mode"] == "snapshot"


def test_sources_returns_static_license_manifest(api_client) -> None:
    response = api_client.get("/api/sources")

    assert response.status_code == 200
    assert "s-maxage=60" in response.headers["cache-control"]
    payload = response.json()
    sources = {item["id"]: item for item in payload["sources"]}
    assert set(sources) == {"seoul-citydata", "overture-places", "openfreemap"}
    assert sources["overture-places"]["release"] == OVERTURE_RELEASE
    assert sources["seoul-citydata"]["licenses"] == [
        {
            "name": "공공누리 제1유형",
            "url": "https://www.kogl.or.kr/info/licenseType1.do",
        }
    ]
    assert any(
        license_link["name"] == "OpenStreetMap ODbL"
        for license_link in sources["openfreemap"]["licenses"]
    )
