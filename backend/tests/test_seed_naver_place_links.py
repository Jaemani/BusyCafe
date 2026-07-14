from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base, Cafe, CafeProviderPlace
from app.schemas import NaverLocalResponse
from scripts.seed_naver_place_links import MATCH_METHOD, main, seed_naver_place_links


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, responses: dict[str, NaverLocalResponse]) -> None:
        self.responses = responses
        self.request_count = 0
        self.queries: list[str] = []

    def search_local(self, query: str) -> NaverLocalResponse:
        self.request_count += 1
        self.queries.append(query)
        return self.responses[query]


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def _cafe(identifier: str, *, address: str | None = None) -> Cafe:
    return Cafe(
        origin_provider="overture",
        origin_source_id=identifier,
        overture_id=identifier,
        source_release="test",
        source_confidence=0.9,
        primary_category="cafe",
        name=f"카페 {identifier}",
        lat=37.55,
        lng=126.98,
        road_address=address,
        active=True,
    )


def _response(identifier: str, address: str, *, place_id: str) -> NaverLocalResponse:
    return NaverLocalResponse.model_validate(
        {
            "total": 1,
            "start": 1,
            "display": 1,
            "items": [
                {
                    "title": f"<b>카페 {identifier}</b>",
                    "link": f"https://map.naver.com/p/entry/place/{place_id}?entry=pll",
                    "roadAddress": address,
                }
            ],
        }
    )


def test_dry_run_then_apply_adds_only_strict_direct_link(session: Session) -> None:
    cafe = _cafe("one", address="서울 종로구 종로 1")
    missing_address = _cafe("missing")
    session.add_all([cafe, missing_address])
    session.commit()
    query = "카페 one 서울 종로구 종로 1"
    response = _response("one", "서울 종로구 종로 1", place_id="123")

    dry = seed_naver_place_links(
        session, FakeClient({query: response}), dry_run=True, now=NOW
    )

    assert dry.eligible_count == 1
    assert dry.searched_count == 1
    assert dry.matched_count == 1
    assert dry.inserted_count == 1
    assert len(dry.accepted_links) == 1
    assert dry.accepted_links[0].cafe_id == cafe.id
    assert dry.accepted_links[0].cafe_name == "카페 one"
    assert dry.accepted_links[0].provider_place_id == "123"
    assert dry.accepted_links[0].detail_url == (
        "https://map.naver.com/p/entry/place/123"
    )
    assert session.scalars(select(CafeProviderPlace)).all() == []

    applied = seed_naver_place_links(
        session, FakeClient({query: response}), dry_run=False, now=NOW
    )

    assert applied.inserted_count == 1
    stored = session.scalar(select(CafeProviderPlace))
    assert stored is not None
    assert stored.cafe_id == cafe.id
    assert stored.provider == "naver"
    assert stored.provider_place_id == "123"
    assert stored.detail_url == "https://map.naver.com/p/entry/place/123"
    assert stored.match_method == MATCH_METHOD
    assert stored.match_distance_m is None


def test_existing_naver_cafe_is_never_requeried_or_replaced(session: Session) -> None:
    linked = _cafe("linked", address="서울 종로구 종로 1")
    target = _cafe("target", address="서울 종로구 종로 2")
    session.add_all([linked, target])
    session.flush()
    session.add(
        CafeProviderPlace(
            cafe_id=linked.id,
            provider="naver",
            provider_place_id="100",
            detail_url="https://map.naver.com/p/entry/place/100",
            active=True,
            match_method="source_direct_url",
            match_distance_m=None,
            verified_at=NOW,
            last_seen_at=NOW,
        )
    )
    session.commit()
    query = "카페 target 서울 종로구 종로 2"
    client = FakeClient(
        {query: _response("target", "서울 종로구 종로 2", place_id="200")}
    )

    report = seed_naver_place_links(session, client, dry_run=True, now=NOW)

    assert report.searched_count == 1
    assert client.queries == [query]


def test_reverse_and_existing_owner_collisions_fail_closed(session: Session) -> None:
    owner = _cafe("owner", address="서울 종로구 종로 1")
    first = _cafe("first", address="서울 종로구 종로 2")
    second = _cafe("second", address="서울 종로구 종로 3")
    third = _cafe("third", address="서울 종로구 종로 4")
    session.add_all([owner, first, second, third])
    session.flush()
    session.add(
        CafeProviderPlace(
            cafe_id=owner.id,
            provider="naver",
            provider_place_id="999",
            detail_url="https://map.naver.com/p/entry/place/999",
            active=True,
            match_method="source_direct_url",
            match_distance_m=None,
            verified_at=NOW,
            last_seen_at=NOW,
        )
    )
    session.commit()
    client = FakeClient(
        {
            "카페 first 서울 종로구 종로 2": _response(
                "first", "서울 종로구 종로 2", place_id="777"
            ),
            "카페 second 서울 종로구 종로 3": _response(
                "second", "서울 종로구 종로 3", place_id="777"
            ),
            "카페 third 서울 종로구 종로 4": _response(
                "third", "서울 종로구 종로 4", place_id="999"
            ),
        }
    )

    report = seed_naver_place_links(session, client, dry_run=False, now=NOW)

    assert report.matched_count == 3
    assert report.inserted_count == 0
    assert dict(report.status_counts)["reverse_collision"] == 2
    assert dict(report.status_counts)["existing_owner_collision"] == 1
    assert len(session.scalars(select(CafeProviderPlace)).all()) == 1


def test_apply_with_no_exact_matches_writes_nothing(session: Session) -> None:
    cafe = _cafe("unmatched", address="서울 종로구 종로 9")
    session.add(cafe)
    session.commit()
    empty = NaverLocalResponse.model_validate(
        {"total": 0, "start": 1, "display": 0, "items": []}
    )

    report = seed_naver_place_links(
        session,
        FakeClient({"카페 unmatched 서울 종로구 종로 9": empty}),
        dry_run=False,
        now=NOW,
    )

    assert report.matched_count == 0
    assert report.inserted_count == 0
    assert report.accepted_links == ()
    assert session.scalars(select(CafeProviderPlace)).all() == []


def test_batch_cursor_and_daily_limit_are_validated(session: Session) -> None:
    cafes = [
        _cafe(str(index), address=f"서울 종로구 종로 {index}")
        for index in range(1, 4)
    ]
    session.add_all(cafes)
    session.commit()
    responses = {
        f"카페 {index} 서울 종로구 종로 {index}": _response(
            str(index), f"서울 종로구 종로 {index}", place_id=str(100 + index)
        )
        for index in range(1, 4)
    }
    client = FakeClient(responses)

    report = seed_naver_place_links(
        session,
        client,
        dry_run=True,
        max_cafes=1,
        after_cafe_id=cafes[0].id,
        now=NOW,
    )

    assert report.searched_count == 1
    assert report.last_searched_cafe_id == cafes[1].id
    assert client.queries == ["카페 2 서울 종로구 종로 2"]
    with pytest.raises(ValueError, match="max_cafes"):
        seed_naver_place_links(session, client, dry_run=True, max_cafes=0)


def test_dry_run_cli_prints_deterministic_accepted_json_without_secrets(
    tmp_path, capsys
) -> None:
    database_path = tmp_path / "naver-dry-run.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_cafe("one", address="서울 종로구 종로 1"))
        session.commit()
    engine.dispose()
    query = "카페 one 서울 종로구 종로 1"
    client = FakeClient(
        {query: _response("one", "서울 종로구 종로 1", place_id="123")}
    )

    assert main(
        ["--database-url", database_url, "--max-cafes", "1"],
        settings_loader=lambda: Settings(
            database_url=database_url,
            naver_client_id=SecretStr("private-id"),
            naver_client_secret=SecretStr("private-secret"),
        ),
        client_factory=lambda _client_id, _client_secret: client,
    ) == 0

    output = capsys.readouterr().out
    assert (
        'accepted: {"cafe_id": 1, "cafe_name": "카페 one", '
        '"detail_url": "https://map.naver.com/p/entry/place/123", '
        '"provider_place_id": "123"}'
    ) in output
    assert "private-id" not in output
    assert "private-secret" not in output
