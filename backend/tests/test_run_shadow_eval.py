from __future__ import annotations

from datetime import UTC, datetime

import pytest
from shapely.geometry import box
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ingest.hotspot_master import HotspotGeometryRecord
from app.models import Base, Hotspot
from scripts.run_shadow_eval import bind_hotspot_geometries, make_polygon_scorer


NOW = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


def geometry_record(*, name: str = "영역 1") -> HotspotGeometryRecord:
    return HotspotGeometryRecord(
        area_cd="POI001",
        name=name,
        category="인구밀집지역",
        geometry_version="fixture-v1",
        normalization="original",
        geometry=box(126.9, 37.5, 127.0, 37.6),
    )


def test_geometry_binding_is_strict_and_polygon_scorer_is_evaluator_compatible() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Hotspot(
                area_cd="POI001",
                name="영역 1",
                category="인구밀집지역",
                lat=37.55,
                lng=126.95,
                is_polled=True,
            )
        )
        session.commit()
        bindings = bind_hotspot_geometries(session, [geometry_record()])
        hotspot_id = next(iter(bindings))

        from app.models import Cafe
        from app.scoring.engine import HotspotObservation

        cafe = Cafe(
            id=1,
            overture_id="place-1",
            source_release="fixture",
            source_confidence=1.0,
            primary_category="cafe",
            name="카페",
            lat=37.55,
            lng=126.95,
            active=True,
        )
        estimate = make_polygon_scorer(bindings)(
            cafe,
            [
                HotspotObservation(
                    hotspot_id=hotspot_id,
                    name="영역 1",
                    lat=37.55,
                    lng=126.95,
                    level=3,
                    observed_at=NOW,
                )
            ],
            NOW,
        )

        assert estimate.coverage == "covered"
        assert estimate.level == 3
        assert estimate.primary_hotspot_id == hotspot_id

        with pytest.raises(ValueError, match="name mismatch"):
            bind_hotspot_geometries(session, [geometry_record(name="다른 이름")])
    engine.dispose()
