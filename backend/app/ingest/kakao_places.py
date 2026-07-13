"""Deterministic, cache-only Kakao CE7 rectangle sweep.

The sweep recursively quarters saturated rectangles before accepting any
documents from them. This avoids the gaps produced by quartering search
circles and never treats Kakao's 45-document exposure cap as complete data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import (
    KAKAO_CAFE_CATEGORY_CODE,
    KAKAO_MAX_PAGES,
    KAKAO_MAX_RESULTS_PER_QUERY,
    KAKAO_PAGE_SIZE,
    KAKAO_SWEEP_MAX_CALLS,
    KAKAO_SWEEP_MAX_DEPTH,
    KAKAO_SWEEP_MIN_CELL_SPAN_DEG,
)
from app.schemas import KakaoCategoryResponse, KakaoPlace


class KakaoCategorySearcher(Protocol):
    def search_category(self, **kwargs: object) -> KakaoCategoryResponse: ...


@dataclass(frozen=True, slots=True)
class SweepCell:
    min_lng: float
    min_lat: float
    max_lng: float
    max_lat: float
    depth: int = 0
    path: str = "root"

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return self.min_lng, self.min_lat, self.max_lng, self.max_lat

    @property
    def width(self) -> float:
        return self.max_lng - self.min_lng

    @property
    def height(self) -> float:
        return self.max_lat - self.min_lat

    def quarters(self) -> tuple["SweepCell", ...]:
        """Return children in stable southwest, southeast, northwest, northeast order."""

        mid_lng = (self.min_lng + self.max_lng) / 2
        mid_lat = (self.min_lat + self.max_lat) / 2
        next_depth = self.depth + 1
        return (
            SweepCell(
                self.min_lng,
                self.min_lat,
                mid_lng,
                mid_lat,
                next_depth,
                f"{self.path}.sw",
            ),
            SweepCell(
                mid_lng,
                self.min_lat,
                self.max_lng,
                mid_lat,
                next_depth,
                f"{self.path}.se",
            ),
            SweepCell(
                self.min_lng,
                mid_lat,
                mid_lng,
                self.max_lat,
                next_depth,
                f"{self.path}.nw",
            ),
            SweepCell(
                mid_lng,
                mid_lat,
                self.max_lng,
                self.max_lat,
                next_depth,
                f"{self.path}.ne",
            ),
        )


@dataclass(frozen=True, slots=True)
class UnresolvedCell:
    path: str
    depth: int
    rect: tuple[float, float, float, float]
    reason: str
    total_count: int | None = None


@dataclass(frozen=True, slots=True)
class KakaoSweepReport:
    records: tuple[KakaoPlace, ...]
    api_calls: int
    http_attempts: int | None
    source_documents: int
    duplicate_documents: int
    completed_leaf_cells: int
    split_cells: int
    max_depth_visited: int
    unresolved: tuple[UnresolvedCell, ...]

    @property
    def complete(self) -> bool:
        return not self.unresolved


def _validate_root(cell: SweepCell) -> None:
    if not (
        -180 <= cell.min_lng < cell.max_lng <= 180
        and -90 <= cell.min_lat < cell.max_lat <= 90
    ):
        raise ValueError("sweep bbox is invalid")
    if cell.depth != 0 or cell.path != "root":
        raise ValueError("root cell must start at depth 0 with path 'root'")


def sweep_kakao_cafes(
    client: KakaoCategorySearcher,
    bbox: tuple[float, float, float, float],
    *,
    max_depth: int = KAKAO_SWEEP_MAX_DEPTH,
    min_cell_span_deg: float = KAKAO_SWEEP_MIN_CELL_SPAN_DEG,
    max_calls: int = KAKAO_SWEEP_MAX_CALLS,
    category_group_code: str = KAKAO_CAFE_CATEGORY_CODE,
) -> KakaoSweepReport:
    """Exhaust a bbox or return a fail-closed report listing unresolved cells."""

    if max_depth < 0:
        raise ValueError("max_depth must be >= 0")
    if min_cell_span_deg <= 0:
        raise ValueError("min_cell_span_deg must be > 0")
    if max_calls <= 0:
        raise ValueError("max_calls must be > 0")
    if not category_group_code.strip():
        raise ValueError("category_group_code must not be empty")

    root = SweepCell(*bbox)
    _validate_root(root)
    records_by_id: dict[str, KakaoPlace] = {}
    unresolved: list[UnresolvedCell] = []
    api_calls = 0
    source_documents = 0
    duplicate_documents = 0
    completed_leaf_cells = 0
    split_cells = 0
    max_depth_visited = 0
    budget_exhausted = False
    initial_http_attempts = getattr(client, "request_count", None)

    def add_unresolved(
        cell: SweepCell, reason: str, total_count: int | None = None
    ) -> None:
        unresolved.append(
            UnresolvedCell(
                path=cell.path,
                depth=cell.depth,
                rect=cell.rect,
                reason=reason,
                total_count=total_count,
            )
        )

    def query(cell: SweepCell, page: int) -> KakaoCategoryResponse | None:
        nonlocal api_calls, budget_exhausted
        if api_calls >= max_calls:
            if not budget_exhausted:
                add_unresolved(cell, "call_budget_exhausted")
                budget_exhausted = True
            return None
        api_calls += 1
        return client.search_category(
            rect=cell.rect,
            page=page,
            size=KAKAO_PAGE_SIZE,
            category_group_code=category_group_code,
        )

    def visit(cell: SweepCell) -> None:
        nonlocal source_documents
        nonlocal duplicate_documents
        nonlocal completed_leaf_cells
        nonlocal split_cells
        nonlocal max_depth_visited

        if budget_exhausted:
            return
        max_depth_visited = max(max_depth_visited, cell.depth)
        first = query(cell, 1)
        if first is None:
            return

        if first.meta.total_count > KAKAO_MAX_RESULTS_PER_QUERY:
            if cell.depth >= max_depth:
                add_unresolved(cell, "max_depth_saturated", first.meta.total_count)
                return
            if (
                cell.width / 2 < min_cell_span_deg
                or cell.height / 2 < min_cell_span_deg
            ):
                add_unresolved(cell, "min_cell_span_saturated", first.meta.total_count)
                return
            split_cells += 1
            for child in cell.quarters():
                visit(child)
            return

        responses = [first]
        current = first
        page = 1
        while not current.meta.is_end:
            if page >= KAKAO_MAX_PAGES:
                add_unresolved(cell, "page_limit_not_end", current.meta.total_count)
                return
            page += 1
            next_response = query(cell, page)
            if next_response is None:
                return
            responses.append(next_response)
            current = next_response

        documents = [document for response in responses for document in response.documents]
        expected_documents = first.meta.pageable_count
        if len(documents) != expected_documents:
            add_unresolved(cell, "pageable_count_mismatch", first.meta.total_count)
            return

        completed_leaf_cells += 1
        source_documents += len(documents)
        for document in documents:
            if document.place_id in records_by_id:
                duplicate_documents += 1
                continue
            records_by_id[document.place_id] = document

    visit(root)
    final_http_attempts = getattr(client, "request_count", None)
    http_attempts = (
        final_http_attempts - initial_http_attempts
        if isinstance(final_http_attempts, int)
        and isinstance(initial_http_attempts, int)
        else None
    )
    return KakaoSweepReport(
        records=tuple(records_by_id[key] for key in sorted(records_by_id)),
        api_calls=api_calls,
        http_attempts=http_attempts,
        source_documents=source_documents,
        duplicate_documents=duplicate_documents,
        completed_leaf_cells=completed_leaf_cells,
        split_cells=split_cells,
        max_depth_visited=max_depth_visited,
        unresolved=tuple(unresolved),
    )
