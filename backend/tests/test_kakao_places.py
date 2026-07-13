from __future__ import annotations

from collections.abc import Callable

from app.ingest.kakao_places import sweep_kakao_cafes
from app.schemas import KakaoCategoryResponse


def _place(identifier: str) -> dict[str, str]:
    return {
        "id": identifier,
        "place_name": f"카페 {identifier}",
        "category_group_code": "CE7",
        "x": "127.0",
        "y": "37.5",
        "place_url": f"http://place.map.kakao.com/{identifier}",
    }


def _response(
    identifiers: list[str],
    *,
    total_count: int | None = None,
    pageable_count: int | None = None,
    is_end: bool = True,
) -> KakaoCategoryResponse:
    return KakaoCategoryResponse.model_validate(
        {
            "meta": {
                "total_count": len(identifiers) if total_count is None else total_count,
                "pageable_count": (
                    len(identifiers) if pageable_count is None else pageable_count
                ),
                "is_end": is_end,
            },
            "documents": [_place(identifier) for identifier in identifiers],
        }
    )


class FakeSearcher:
    def __init__(
        self,
        handler: Callable[
            [tuple[float, float, float, float], int], KakaoCategoryResponse
        ],
    ) -> None:
        self.handler = handler
        self.request_count = 0

    def search_category(self, **kwargs: object) -> KakaoCategoryResponse:
        self.request_count += 1
        return self.handler(kwargs["rect"], kwargs["page"])  # type: ignore[arg-type]


def test_saturated_rectangle_is_quartered_and_place_ids_are_deduped() -> None:
    root = (126.0, 37.0, 128.0, 39.0)
    children = {
        (126.0, 37.0, 127.0, 38.0): ["1", "shared"],
        (127.0, 37.0, 128.0, 38.0): ["2", "shared"],
        (126.0, 38.0, 127.0, 39.0): ["3", "4"],
        (127.0, 38.0, 128.0, 39.0): ["5", "6"],
    }

    def handler(rect: tuple[float, float, float, float], page: int):
        assert page == 1
        if rect == root:
            return _response(
                [str(index) for index in range(15)],
                total_count=46,
                pageable_count=45,
                is_end=False,
            )
        return _response(children[rect])

    report = sweep_kakao_cafes(FakeSearcher(handler), root)

    assert report.complete
    assert [record.place_id for record in report.records] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "shared",
    ]
    assert report.api_calls == 5
    assert report.http_attempts == 5
    assert report.source_documents == 8
    assert report.duplicate_documents == 1
    assert report.completed_leaf_cells == 4
    assert report.split_cells == 1
    assert report.max_depth_visited == 1


def test_unsaturated_rectangle_follows_all_three_pages() -> None:
    identifiers = [str(index) for index in range(31)]

    def handler(_rect: tuple[float, float, float, float], page: int):
        start = (page - 1) * 15
        page_ids = identifiers[start : start + 15]
        return _response(
            page_ids,
            total_count=31,
            pageable_count=31,
            is_end=page == 3,
        )

    report = sweep_kakao_cafes(
        FakeSearcher(handler), (126.0, 37.0, 127.0, 38.0)
    )

    assert report.complete
    assert len(report.records) == 31
    assert report.api_calls == 3
    assert report.completed_leaf_cells == 1


def test_saturated_cell_at_max_depth_is_fail_closed() -> None:
    searcher = FakeSearcher(
        lambda _rect, _page: _response(
            [str(index) for index in range(15)],
            total_count=46,
            pageable_count=45,
            is_end=False,
        )
    )

    report = sweep_kakao_cafes(
        searcher, (126.0, 37.0, 127.0, 38.0), max_depth=0
    )

    assert not report.complete
    assert report.records == ()
    assert report.unresolved[0].reason == "max_depth_saturated"
    assert report.unresolved[0].total_count == 46


def test_saturated_cell_below_minimum_child_span_is_fail_closed() -> None:
    searcher = FakeSearcher(
        lambda _rect, _page: _response([], total_count=100, pageable_count=45)
    )

    report = sweep_kakao_cafes(
        searcher,
        (126.0, 37.0, 126.00008, 37.00008),
        min_cell_span_deg=0.00005,
    )

    assert not report.complete
    assert report.unresolved[0].reason == "min_cell_span_saturated"


def test_call_budget_stops_the_sweep_with_one_explicit_failure() -> None:
    searcher = FakeSearcher(
        lambda _rect, _page: _response([], total_count=100, pageable_count=45)
    )

    report = sweep_kakao_cafes(
        searcher, (126.0, 37.0, 127.0, 38.0), max_calls=1
    )

    assert not report.complete
    assert report.api_calls == 1
    assert len(report.unresolved) == 1
    assert report.unresolved[0].reason == "call_budget_exhausted"


def test_pageable_count_mismatch_is_never_published_as_complete() -> None:
    report = sweep_kakao_cafes(
        FakeSearcher(
            lambda _rect, _page: _response(
                ["1"], total_count=2, pageable_count=2, is_end=True
            )
        ),
        (126.0, 37.0, 127.0, 38.0),
    )

    assert not report.complete
    assert report.records == ()
    assert report.unresolved[0].reason == "pageable_count_mismatch"


def test_invalid_sweep_limits_are_rejected_before_calling_provider() -> None:
    searcher = FakeSearcher(lambda _rect, _page: _response([]))

    for kwargs in (
        {"max_depth": -1},
        {"min_cell_span_deg": 0},
        {"max_calls": 0},
    ):
        try:
            sweep_kakao_cafes(
                searcher, (126.0, 37.0, 127.0, 38.0), **kwargs
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid limit failure: {kwargs}")
    assert searcher.request_count == 0
