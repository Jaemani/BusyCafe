from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Cafe, CafeProviderPlace
from scripts.report_catalog_gaps import (
    build_catalog_gap_report,
    enforce_transaction_read_only,
)


NOW = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def _cafe(
    identifier: str,
    *,
    origin: str = "overture",
    address: str | None = "서울 종로구 종로 1",
    active: bool = True,
) -> Cafe:
    return Cafe(
        origin_provider=origin,
        origin_source_id=identifier,
        overture_id=identifier if origin == "overture" else None,
        source_release="test",
        source_confidence=1.0,
        primary_category="cafe",
        name=f"카페 {identifier}",
        lat=37.55,
        lng=126.98,
        road_address=address,
        active=active,
    )


def _link(
    cafe: Cafe,
    provider: str,
    place_id: str,
    detail_url: str,
    *,
    active: bool = True,
) -> CafeProviderPlace:
    return CafeProviderPlace(
        cafe_id=cafe.id,
        provider=provider,
        provider_place_id=place_id,
        detail_url=detail_url,
        active=active,
        match_method="test",
        match_distance_m=None,
        verified_at=NOW,
        last_seen_at=NOW,
    )


def test_report_counts_origin_provider_link_validity_and_exact_batches(
    session: Session,
) -> None:
    linked = _cafe("linked")
    eligible = _cafe(
        "eligible",
        origin="seoul_refreshment_permits",
        address="서울 중구 세종대로 1",
    )
    no_address = _cafe("no-address", address=None)
    inactive = _cafe("inactive", active=False)
    session.add_all([linked, eligible, no_address, inactive])
    session.flush()
    session.add_all(
        [
            _link(
                linked,
                "kakao",
                "100",
                "https://place.map.kakao.com/100",
            ),
            _link(
                linked,
                "naver",
                "200",
                "https://map.naver.com/p/entry/place/200",
            ),
            _link(
                eligible,
                "kakao",
                "300",
                "https://place.map.kakao.com/WRONG",
            ),
            _link(
                inactive,
                "naver",
                "400",
                "https://map.naver.com/p/entry/place/400",
            ),
        ]
    )
    session.commit()

    report = build_catalog_gap_report(session, batch_size=1)

    assert report.active_cafes_total == 3
    assert report.active_cafes_by_origin == {
        "overture": 2,
        "seoul_refreshment_permits": 1,
    }
    kakao = report.provider_coverage["kakao"]
    assert kakao.rows_for_active_cafes == 2
    assert kakao.active_rows == 2
    assert kakao.inactive_rows == 0
    assert kakao.valid_direct_links == 1
    assert kakao.invalid_direct_links == 1
    assert kakao.active_cafes_with_identity == 2
    assert kakao.active_cafes_with_valid_direct_link == 1
    assert kakao.active_cafes_without_valid_direct_link == 2
    naver = report.provider_coverage["naver"]
    assert naver.rows_for_active_cafes == 1
    assert naver.valid_direct_links == 1
    assert naver.active_cafes_with_identity == 1
    assert naver.active_cafes_with_valid_direct_link == 1
    assert naver.active_cafes_without_valid_direct_link == 2
    assert report.naver_exact_match_eligible_total == 1
    assert report.naver_exact_match_eligible_by_origin == {
        "seoul_refreshment_permits": 1
    }
    assert report.naver_exact_match_missing_road_address_total == 1
    assert report.naver_exact_match_batch_size == 1
    assert report.naver_exact_match_batch_count == 1
    matrix = {
        (row.origin, row.provider): (row.active_links, row.valid_direct_links)
        for row in report.origin_provider_coverage
    }
    assert matrix[("overture", "kakao")] == (1, 1)
    assert matrix[("overture", "naver")] == (1, 1)
    assert matrix[("seoul_refreshment_permits", "kakao")] == (1, 0)
    assert matrix[("seoul_refreshment_permits", "naver")] == (0, 0)


def test_inactive_naver_identity_is_not_requeried_by_exact_match_batches(
    session: Session,
) -> None:
    cafe = _cafe("inactive-naver")
    session.add(cafe)
    session.flush()
    session.add(
        _link(
            cafe,
            "naver",
            "500",
            "https://map.naver.com/p/entry/place/500",
            active=False,
        )
    )
    session.commit()

    report = build_catalog_gap_report(session)

    assert report.provider_coverage["naver"].rows_for_active_cafes == 1
    assert report.provider_coverage["naver"].active_rows == 0
    assert report.provider_coverage["naver"].inactive_rows == 1
    assert report.provider_coverage["naver"].active_cafes_with_identity == 0
    assert (
        report.provider_coverage["naver"].active_cafes_with_valid_direct_link
        == 0
    )
    assert (
        report.provider_coverage["naver"].active_cafes_without_valid_direct_link
        == 1
    )
    assert report.naver_exact_match_eligible_total == 0


def test_postgresql_transaction_is_explicitly_read_only() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement):
            self.statements.append(str(statement))

    session = FakeSession()

    enforce_transaction_read_only(session)

    assert session.statements == ["SET TRANSACTION READ ONLY"]


def test_report_rejects_nonpositive_batch_size_before_query(session: Session) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        build_catalog_gap_report(session, batch_size=0)


def test_production_workflow_is_manual_db_only_and_read_only() -> None:
    workflow = (
        ROOT / ".github/workflows/report-catalog-gaps-production.yml"
    ).read_text(encoding="utf-8")
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "environment: Production" in workflow
    assert "timeout-minutes: 5" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "scripts/report_catalog_gaps.py" in workflow
    assert "NAVER_CLIENT" not in workflow
    assert "KAKAO_REST" not in workflow
    assert "--apply" not in workflow
    assert "curl " not in workflow
