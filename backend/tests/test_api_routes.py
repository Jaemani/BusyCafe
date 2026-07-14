from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import _observation_age_minutes, _observation_freshness
from app.config import (
    CURRENT_DISPLAY_MAX_AGE_MIN,
    FRESHNESS_MAX_FUTURE_SKEW_MIN,
    OVERTURE_RELEASE,
    SCORING_MODEL_VERSION,
)
from app.database import get_db
from app.main import (
    HEALTH_CACHE_CONTROL,
    MAP_CACHE_CONTROL,
    STATIC_CACHE_CONTROL,
    create_app,
)
from app.models import (
    Base,
    Cafe,
    CafeProviderPlace,
    CafeScore,
    Hotspot,
    HotspotServingState,
    HotspotSnapshot,
    IngestCycle,
)
from app.schemas import KakaoPlace
from scripts.seed_kakao_catalog_expansion import (
    ValidatedKakaoSnapshot,
    seed_kakao_catalog_expansion,
)


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
                source_observed_at=now,
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
            HotspotServingState(
                hotspot_id=hotspot.id,
                computed_at=now,
                observed_at=now,
                trend_12h_json=[
                    {"observed_at": now.isoformat(), "level": 2}
                ],
                forecast_1h_json={
                    "FCST_TIME": "precomputed",
                    "FCST_CONGEST_LVL": "보통",
                },
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
    assert response.headers["cache-control"] == MAP_CACHE_CONTROL
    assert response.headers["x-busycafe-viewport-truncated"] == "false"
    payload = response.json()
    assert [item["name"] for item in payload] == ["정확한 카페"]
    assert payload[0]["coverage"] == "covered"
    assert payload[0]["freshness"] == "fresh"
    assert set(payload[0]) == {
        "id",
        "name",
        "lat",
        "lng",
        "level",
        "confidence",
        "freshness",
        "coverage",
        "evidence",
    }
    assert payload[0]["evidence"]["hotspot_name"] == "테스트 핫스팟"
    assert payload[0]["evidence"]["observed_at"] is not None
    assert payload[0]["evidence"]["observed_at"].endswith("Z")
    assert payload[0]["evidence"]["age_minutes"] in (0, 1)
    detail = api_client.get(f"/api/cafes/{payload[0]['id']}").json()
    assert detail["phone"] == "02-123-4567"
    assert detail["website"] == "https://example.test"
    assert detail["external_links"]["kakao"].endswith("/456")
    assert detail["external_links"]["naver"].endswith("/123")
    assert detail["external_links"]["naver_search"] is None
    assert detail["trend_12h"][0]["level"] == 2
    assert detail["forecast_1h"] == {
        "FCST_TIME": "precomputed",
        "FCST_CONGEST_LVL": "보통",
    }


def test_kakao_origin_place_fields_reach_map_and_detail_api(api_client) -> None:
    generated_at = datetime(2026, 7, 14, 12, 34, 56, tzinfo=UTC)
    place = KakaoPlace.model_validate(
        {
            "id": "987654321",
            "place_name": "카카오 원장 카페",
            "category_name": "음식점 > 카페",
            "category_group_code": "CE7",
            "category_group_name": "카페",
            "phone": "02-9876-5432",
            "address_name": "서울 성동구 성수동1가 10-1",
            "road_address_name": "",
            "x": 127.02,
            "y": 37.56,
            "place_url": "http://place.map.kakao.com/987654321",
            "distance": "",
        }
    )
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        report = seed_kakao_catalog_expansion(
            session,
            ValidatedKakaoSnapshot(
                places=(place,),
                generated_at=generated_at,
                source_release=generated_at.isoformat(),
            ),
            apply=True,
            max_expected_candidates=1,
        )
        assert report.inserted_cafe_count == 1
        cafe = session.scalar(
            select(Cafe).where(
                Cafe.origin_provider == "kakao",
                Cafe.origin_source_id == "987654321",
            )
        )
        assert cafe is not None
        cafe_id = cafe.id
        assert cafe.source_json == [
            {
                "provider": "kakao",
                "provider_place_id": "987654321",
                "category": "음식점 > 카페",
                "road_address": None,
                "lot_address": "서울 성동구 성수동1가 10-1",
                "phone": "02-9876-5432",
                "direct_url": "https://place.map.kakao.com/987654321",
            }
        ]

    summary = api_client.get(
        "/api/cafes/summary",
        params={"bbox": "127.01,37.55,127.03,37.57"},
    )
    assert summary.status_code == 200
    marker = next(item for item in summary.json() if item["id"] == cafe_id)
    assert marker["name"] == "카카오 원장 카페"
    assert marker["lat"] == 37.56
    assert marker["lng"] == 127.02

    response = api_client.get(f"/api/cafes/{cafe_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["name"] == "카카오 원장 카페"
    assert detail["lat"] == 37.56
    assert detail["lng"] == 127.02
    assert detail["road_address"] == "서울 성동구 성수동1가 10-1"
    assert detail["phone"] == "02-9876-5432"
    assert detail["external_links"]["kakao"] == (
        "https://place.map.kakao.com/987654321"
    )


def test_bbox_summary_omits_lazy_evidence_but_keeps_map_state(api_client) -> None:
    response = api_client.get(
        "/api/cafes/summary",
        params={
            "bbox": "126.9,37.5,127.1,37.7",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == MAP_CACHE_CONTROL
    payload = response.json()
    assert len(payload) == 1
    assert set(payload[0]) == {
        "id",
        "name",
        "lat",
        "lng",
        "level",
        "confidence",
        "freshness",
        "coverage",
        "age_minutes",
    }
    assert payload[0]["age_minutes"] in (0, 1)
    assert payload[0]["freshness"] == "fresh"
    assert payload[0]["level"] == 2
    assert payload[0]["coverage"] == "covered"


def test_api_request_modules_never_import_scoring_code() -> None:
    api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
    violations: list[str] = []
    for path in sorted(api_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).startswith("app.scoring"):
                violations.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.Import) and any(
                alias.name.startswith("app.scoring") for alias in node.names
            ):
                violations.append(f"{path.name}:{node.lineno}")
    assert violations == []


def test_bbox_api_fails_closed_and_signals_when_viewport_exceeds_cap(
    api_client, monkeypatch
) -> None:
    from app.api import routes

    monkeypatch.setattr(routes, "MAX_CAFES_PER_VIEWPORT", 1)

    exact_cap = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    )
    assert len(exact_cap.json()) == 1
    assert exact_cap.headers["x-busycafe-viewport-truncated"] == "false"

    factory = api_client.app.state.test_session_factory
    with factory() as session:
        session.add(
            Cafe(
                overture_id="overture:test-over-cap",
                source_release="2026-06-17.0",
                source_confidence=0.9,
                primary_category="cafe",
                name="두 번째 카페",
                lat=37.553,
                lng=126.983,
            )
        )
        session.commit()

    truncated = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
        headers={"Origin": "http://localhost:5188"},
    )
    assert truncated.status_code == 200
    assert truncated.json() == []
    assert truncated.headers["x-busycafe-viewport-truncated"] == "true"
    assert (
        truncated.headers["access-control-expose-headers"]
        == "X-BusyCafe-Viewport-Truncated"
    )
    summary_truncated = api_client.get(
        "/api/cafes/summary",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    )
    assert summary_truncated.status_code == 200
    assert summary_truncated.json() == []
    assert summary_truncated.headers["x-busycafe-viewport-truncated"] == "true"


def test_detail_api_returns_scoring_model_version(api_client) -> None:
    listing = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    ).json()

    response = api_client.get(f"/api/cafes/{listing[0]['id']}")

    assert response.status_code == 200
    assert response.json()["model_version"] == SCORING_MODEL_VERSION
    assert response.json()["freshness"] == "fresh"
    assert response.headers["cache-control"] == MAP_CACHE_CONTROL


def test_cafe_reads_never_scan_raw_snapshots(api_client) -> None:
    factory = api_client.app.state.test_session_factory
    engine = factory.kw["bind"]
    selected_statements: list[str] = []

    def capture_selects(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            selected_statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_selects)
    try:
        listing = api_client.get(
            "/api/cafes",
            params={"bbox": "126.9,37.5,127.1,37.7"},
        ).json()
        response = api_client.get(f"/api/cafes/{listing[0]['id']}")
    finally:
        event.remove(engine, "before_cursor_execute", capture_selects)

    assert response.status_code == 200
    cafe_reads = "\n".join(selected_statements)
    assert "hotspot_snapshots" not in cafe_reads
    assert "hotspot_serving_states" in cafe_reads


def test_bbox_read_omits_unused_score_and_hotspot_columns(api_client) -> None:
    factory = api_client.app.state.test_session_factory
    engine = factory.kw["bind"]
    selected_statements: list[str] = []

    def capture_selects(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selected_statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_selects)
    try:
        response = api_client.get(
            "/api/cafes",
            params={"bbox": "126.9,37.5,127.1,37.7"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_selects)

    assert response.status_code == 200
    assert len(selected_statements) == 1
    statement = selected_statements[0]
    assert "cafe_scores.contributors_json" not in statement
    assert "cafe_scores.model_version" not in statement
    assert "cafe_scores.computed_at" not in statement
    assert "cafe_scores.confidence_tier" not in statement
    assert "hotspots.category" not in statement
    assert "hotspots.lat" not in statement
    assert "hotspots.lng" not in statement


def test_public_read_cache_policies_match_mutability(api_client) -> None:
    hotspots = api_client.get("/api/hotspots")
    health = api_client.get("/api/health")
    sources = api_client.get("/api/sources")

    assert hotspots.headers["cache-control"] == MAP_CACHE_CONTROL
    assert health.headers["cache-control"] == HEALTH_CACHE_CONTROL
    assert sources.headers["cache-control"] == STATIC_CACHE_CONTROL


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/api/cafes?bbox=bad", {}),
        ("/api/cafes/999999", {}),
        (
            "/api/cafes?bbox=126.9,37.5,127.1,37.7",
            {"Authorization": "Bearer must-not-be-shared"},
        ),
    ],
)
def test_errors_and_authenticated_requests_are_not_publicly_cached(
    api_client,
    path: str,
    headers: dict[str, str],
) -> None:
    response = api_client.get(path, headers=headers)

    assert response.headers.get("cache-control") is None


def test_cafe_source_label_exposes_permit_verification(api_client) -> None:
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        cafe = session.query(Cafe).filter(Cafe.active.is_(True)).one()
        cafe.source_json = [
            {
                "dataset_id": "OA-16095",
                "management_number": "verified-test",
            }
        ]
        session.commit()

    listing_item = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    ).json()[0]
    item = api_client.get(f"/api/cafes/{listing_item['id']}").json()

    assert "서울시 영업 인허가 대조" in item["source_label"]


def test_delayed_snapshot_shows_level_without_confidence(api_client) -> None:
    stale_time = datetime.now(UTC) - timedelta(minutes=26)
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        snapshot = session.query(HotspotSnapshot).one()
        snapshot.observed_at = stale_time
        snapshot.fetched_at = stale_time
        session.query(CafeScore).one().source_observed_at = stale_time
        serving_state = session.query(HotspotServingState).one()
        serving_state.observed_at = stale_time
        serving_state.trend_12h_json = [
            {"observed_at": stale_time.isoformat(), "level": 2}
        ]
        session.commit()

    listing = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    ).json()

    assert len(listing) == 1
    item = listing[0]
    assert item["freshness"] == "delayed"
    assert item["level"] == 2
    assert "score" not in item
    assert item["confidence"] is None
    assert "confidence_tier" not in item
    assert item["coverage"] == "covered"
    assert "model_version" not in item
    assert item["evidence"]["hotspot_name"] == "테스트 핫스팟"
    assert item["evidence"]["observed_at"] is not None
    assert item["evidence"]["age_minutes"] in (26, 27)
    assert api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7", "min_conf": 0.1},
    ).json() == []

    detail = api_client.get(f"/api/cafes/{item['id']}").json()
    assert detail["freshness"] == "delayed"
    assert detail["level"] == 2
    assert detail["score"] == 2.0
    assert detail["forecast_1h"] is None

    hotspot = api_client.get("/api/hotspots").json()[0]
    assert hotspot["freshness"] == "delayed"
    assert hotspot["level"] == 2
    assert hotspot["observed_at"] is not None


def test_stale_snapshot_preserves_evidence_but_hides_current_score(api_client) -> None:
    stale_time = datetime.now(UTC) - timedelta(
        minutes=CURRENT_DISPLAY_MAX_AGE_MIN + 1
    )
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        snapshot = session.query(HotspotSnapshot).one()
        snapshot.observed_at = stale_time
        snapshot.fetched_at = stale_time
        session.query(CafeScore).one().source_observed_at = stale_time
        serving_state = session.query(HotspotServingState).one()
        serving_state.observed_at = stale_time
        serving_state.trend_12h_json = [
            {"observed_at": stale_time.isoformat(), "level": 2}
        ]
        session.commit()

    item = api_client.get(
        "/api/cafes",
        params={"bbox": "126.9,37.5,127.1,37.7"},
    ).json()[0]

    assert item["freshness"] == "stale"
    assert item["level"] is None
    assert "score" not in item
    assert item["confidence"] is None
    assert "confidence_tier" not in item
    assert item["coverage"] == "covered"
    assert item["evidence"]["observed_at"] is not None


def test_observation_freshness_boundary_and_future_skew() -> None:
    now = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)

    assert _observation_freshness(
        now - timedelta(minutes=25), now=now
    ) == "fresh"
    assert _observation_freshness(
        now - timedelta(minutes=25, microseconds=1), now=now
    ) == "delayed"
    assert _observation_freshness(
        now - timedelta(minutes=CURRENT_DISPLAY_MAX_AGE_MIN), now=now
    ) == "delayed"
    assert _observation_freshness(
        now
        - timedelta(
            minutes=CURRENT_DISPLAY_MAX_AGE_MIN,
            microseconds=1,
        ),
        now=now,
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


def test_observation_age_rounds_up_and_rejects_invalid_future_time() -> None:
    now = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)

    assert _observation_age_minutes(now, now=now) == 0
    assert _observation_age_minutes(
        now - timedelta(minutes=25, seconds=1), now=now
    ) == 26
    assert _observation_age_minutes(
        now + timedelta(minutes=FRESHNESS_MAX_FUTURE_SKEW_MIN), now=now
    ) == 0
    assert _observation_age_minutes(
        now + timedelta(minutes=FRESHNESS_MAX_FUTURE_SKEW_MIN, seconds=1),
        now=now,
    ) is None
    assert _observation_age_minutes(None, now=now) is None


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


def test_naver_search_fallback_uses_encoded_address_then_name(api_client) -> None:
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        cafe = session.query(Cafe).filter(Cafe.active.is_(True)).one()
        cafe.external_links_json = {
            "kakao": "https://place.map.kakao.com/456",
            "naver_search": "https://evil.example/injected",
        }
        cafe.road_address = " 서울시  테스트구 /1?x=# "
        cafe.name = " 정확한   카페%점 "
        cafe_id = cafe.id
        session.commit()

    item = api_client.get(f"/api/cafes/{cafe_id}").json()
    expected_query = "서울시 테스트구 /1?x=# 정확한 카페%점"

    assert item["external_links"]["naver"] is None
    assert item["external_links"]["naver_search"] == (
        f"https://map.naver.com/p/search/{quote(expected_query, safe='')}"
    )
    assert "evil.example" not in item["external_links"]["naver_search"]


def test_naver_search_fallback_requires_road_address(api_client) -> None:
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        cafe = session.query(Cafe).filter(Cafe.active.is_(True)).one()
        cafe.external_links_json = {
            "kakao": "https://place.map.kakao.com/456",
        }
        cafe.road_address = None
        cafe_id = cafe.id
        session.commit()

    item = api_client.get(f"/api/cafes/{cafe_id}").json()

    assert item["external_links"]["naver"] is None
    assert item["external_links"]["naver_search"] is None


def test_provider_table_links_override_json_and_mobile_naver_is_direct(
    api_client,
) -> None:
    now = datetime.now(UTC)
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        cafe = session.query(Cafe).filter(Cafe.active.is_(True)).one()
        session.add(
            CafeProviderPlace(
                cafe_id=cafe.id,
                provider="naver",
                provider_place_id="999",
                detail_url="https://m.place.naver.com/restaurant/999/home?entry=pll",
                match_method="source_direct_url",
                verified_at=now,
                last_seen_at=now,
            )
        )
        session.commit()

    listing_item = api_client.get(
        "/api/cafes", params={"bbox": "126.9,37.5,127.1,37.7"}
    ).json()[0]
    item = api_client.get(f"/api/cafes/{listing_item['id']}").json()

    assert item["external_links"]["naver"].startswith(
        "https://m.place.naver.com/restaurant/999/"
    )
    assert item["external_links"]["naver_search"] is None
    assert item["external_links"]["kakao"].endswith("/456")


def test_inactive_provider_row_suppresses_legacy_fallback(api_client) -> None:
    now = datetime.now(UTC)
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        cafe = session.query(Cafe).filter(Cafe.active.is_(True)).one()
        session.add(
            CafeProviderPlace(
                cafe_id=cafe.id,
                provider="naver",
                provider_place_id="123",
                detail_url="https://map.naver.com/p/entry/place/123",
                active=False,
                match_method="source_direct_url",
                verified_at=now,
                last_seen_at=now,
            )
        )
        session.commit()

    listing_item = api_client.get(
        "/api/cafes", params={"bbox": "126.9,37.5,127.1,37.7"}
    ).json()[0]
    item = api_client.get(f"/api/cafes/{listing_item['id']}").json()

    assert item["external_links"]["naver"] is None
    assert item["external_links"]["kakao"].endswith("/456")


@pytest.mark.parametrize(
    ("provider", "provider_place_id", "detail_url"),
    [
        (
            "naver",
            "999",
            "https://m.place.naver.com/restaurant/998/home",
        ),
        ("kakao", "999", "https://place.map.kakao.com/998"),
        (
            "google",
            "ChIJ-provider-999",
            (
                "https://www.google.com/maps/search/?api=1&"
                "query_place_id=ChIJ-provider-998"
            ),
        ),
    ],
)
def test_provider_row_url_must_embed_matching_place_id(
    api_client,
    provider: str,
    provider_place_id: str,
    detail_url: str,
) -> None:
    now = datetime.now(UTC)
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        cafe = session.query(Cafe).filter(Cafe.active.is_(True)).one()
        session.add(
            CafeProviderPlace(
                cafe_id=cafe.id,
                provider=provider,
                provider_place_id=provider_place_id,
                detail_url=detail_url,
                match_method="source_direct_url",
                verified_at=now,
                last_seen_at=now,
            )
        )
        session.commit()

    listing_item = api_client.get(
        "/api/cafes", params={"bbox": "126.9,37.5,127.1,37.7"}
    ).json()[0]
    item = api_client.get(f"/api/cafes/{listing_item['id']}").json()

    assert item["external_links"][provider] is None


@pytest.mark.parametrize(
    "candidate",
    [
        "https://map.naver.com/p/search/cafe/place/1847575540",
        "https://naver.me/GGUnSHuz",
        "https://m.place.naver.com/restaurant/not-numeric",
        "https://m.place.naver.com/restaurant/１２３",
        "https://place.map.kakao.com/not-numeric",
        "https://place.map.kakao.com/１２３",
        "http://place.map.kakao.com/24725284",
    ],
)
def test_noncanonical_provider_links_are_rejected(candidate: str) -> None:
    from app.api.routes import _safe_external_links

    provider = "kakao" if "kakao" in candidate else "naver"
    links = _safe_external_links({provider: candidate})

    assert getattr(links, provider) is None


def test_source_label_uses_canonical_origin(api_client) -> None:
    factory = api_client.app.state.test_session_factory
    with factory() as session:
        cafe = session.query(Cafe).filter(Cafe.active.is_(True)).one()
        cafe.origin_provider = "kakao"
        cafe.origin_source_id = "456"
        session.commit()

    listing_item = api_client.get(
        "/api/cafes", params={"bbox": "126.9,37.5,127.1,37.7"}
    ).json()[0]
    item = api_client.get(f"/api/cafes/{listing_item['id']}").json()

    assert item["source_label"].startswith("카카오맵 등록 장소 ·")


def test_health_counts_only_active_cafes(api_client, monkeypatch) -> None:
    monkeypatch.delenv("CAFE_CROWD_SNAPSHOT", raising=False)

    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "live"
    assert response.json()["stale_warn_min"] == 25
    assert response.json()["current_display_max_age_min"] == 120
    assert response.json()["cafes_count"] == 1
    assert response.json()["snapshots_last_hour"] == 1
    assert response.json()["last_ingest_at"].endswith("Z")
    assert response.json()["last_complete_cycle_at"].endswith("Z")
    assert response.json()["last_cycle_status"] == "running"
    assert response.json()["last_cycle_targets"] == 1
    assert response.json()["last_cycle_saved"] == 0
    assert response.json()["last_cycle_failed"] == 0


def test_health_uses_one_database_round_trip(api_client) -> None:
    factory = api_client.app.state.test_session_factory
    engine = factory.kw["bind"]
    selected_statements: list[str] = []

    def capture_selects(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            selected_statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_selects)
    try:
        response = api_client.get("/api/health")
    finally:
        event.remove(engine, "before_cursor_execute", capture_selects)

    assert response.status_code == 200
    assert len(selected_statements) == 1


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
    assert response.headers["cache-control"] == STATIC_CACHE_CONTROL
    payload = response.json()
    sources = {item["id"]: item for item in payload["sources"]}
    assert set(sources) == {
        "seoul-citydata",
        "seoul-refreshment-permits",
        "overture-places",
        "kakao-local",
        "openfreemap",
    }
    assert sources["overture-places"]["release"] == OVERTURE_RELEASE
    assert sources["seoul-citydata"]["licenses"] == [
        {
            "name": "공공누리 제1유형",
            "url": "https://www.kogl.or.kr/info/licenseType1.do",
        }
    ]
    assert sources["seoul-refreshment-permits"]["role"] == "place_verification"
    assert sources["kakao-local"]["role"] == "place_catalog_and_identity"
    assert sources["kakao-local"]["licenses"] == [
        {
            "name": "Kakao API 운영정책",
            "url": (
                "https://developers.kakao.com/terms/latest/ko/site-policies"
            ),
        }
    ]
    assert any(
        license_link["name"] == "OpenStreetMap ODbL"
        for license_link in sources["openfreemap"]["licenses"]
    )
