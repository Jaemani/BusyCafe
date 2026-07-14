"""Select deterministic Phase 6 field-observation candidates from the DB cache."""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import (
    COVERED_M,
    EVAL_CANDIDATES_PER_BAND,
    EVAL_DEFAULT_HOTSPOT_NAMES,
    EVAL_NEAR_MAX_M,
    R_MAX_M,
)
from app.database import create_db_engine
from app.models import Cafe, CafeProviderPlace, CafeScore, Hotspot


CSV_FIELDS = (
    "cafe_id",
    "name",
    "road_address",
    "lat",
    "lng",
    "hotspot_name",
    "distance_band",
    "primary_distance_m",
    "source_confidence",
    "kakao_url",
    "naver_url",
    "poi_valid",
    "exclusion_reason",
)
BAND_ORDER = ("near", "mid", "fringe")


@dataclass(frozen=True, slots=True)
class Candidate:
    cafe_id: int
    name: str
    road_address: str | None
    lat: float
    lng: float
    hotspot_name: str
    distance_band: str
    primary_distance_m: float
    source_confidence: float
    kakao_url: str | None
    naver_url: str | None


@dataclass(frozen=True, slots=True)
class SelectionResult:
    candidates: tuple[Candidate, ...]
    shortages: tuple[tuple[str, str, int, int], ...]


def _distance_band(distance_m: float) -> str | None:
    if distance_m <= EVAL_NEAR_MAX_M:
        return "near"
    if distance_m <= COVERED_M:
        return "mid"
    if distance_m <= R_MAX_M:
        return "fringe"
    return None


def select_candidates(
    session: Session,
    hotspot_names: Sequence[str] = EVAL_DEFAULT_HOTSPOT_NAMES,
    *,
    per_band: int = EVAL_CANDIDATES_PER_BAND,
) -> SelectionResult:
    """Select up to ``per_band`` active cafes for each hotspot and band."""

    if per_band < 1:
        raise ValueError("per_band must be positive")
    requested_names = tuple(dict.fromkeys(hotspot_names))
    if not requested_names:
        raise ValueError("at least one hotspot name is required")

    rows = session.execute(
        select(Cafe, CafeScore, Hotspot)
        .options(selectinload(Cafe.provider_places))
        .join(CafeScore, CafeScore.cafe_id == Cafe.id)
        .join(Hotspot, Hotspot.id == CafeScore.primary_hotspot_id)
        .where(
            Cafe.active.is_(True),
            Hotspot.name.in_(requested_names),
            CafeScore.primary_distance_m.is_not(None),
            CafeScore.primary_distance_m <= R_MAX_M,
        )
    ).all()
    grouped: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for cafe, score, hotspot in rows:
        distance_m = score.primary_distance_m
        if distance_m is None:
            continue
        band = _distance_band(distance_m)
        if band is None:
            continue
        direct_links = {
            place.provider: place.detail_url
            for place in cafe.provider_places
            if place.active
            and place.detail_url
            and place.provider in {"kakao", "naver"}
        }
        grouped[(hotspot.name, band)].append(
            Candidate(
                cafe_id=cafe.id,
                name=cafe.name,
                road_address=cafe.road_address,
                lat=cafe.lat,
                lng=cafe.lng,
                hotspot_name=hotspot.name,
                distance_band=band,
                primary_distance_m=distance_m,
                source_confidence=cafe.source_confidence,
                kakao_url=direct_links.get("kakao"),
                naver_url=direct_links.get("naver"),
            )
        )

    selected: list[Candidate] = []
    shortages: list[tuple[str, str, int, int]] = []
    for hotspot_name in requested_names:
        for band in BAND_ORDER:
            candidates = sorted(
                grouped[(hotspot_name, band)],
                key=lambda item: (
                    -item.source_confidence,
                    item.primary_distance_m,
                    item.cafe_id,
                ),
            )
            chosen = candidates[:per_band]
            selected.extend(chosen)
            if len(chosen) < per_band:
                shortages.append((hotspot_name, band, len(chosen), per_band))
    return SelectionResult(tuple(selected), tuple(shortages))


def render_csv(candidates: Sequence[Candidate]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for item in candidates:
        writer.writerow(
            {
                "cafe_id": item.cafe_id,
                "name": item.name,
                "road_address": item.road_address or "",
                "lat": f"{item.lat:.7f}",
                "lng": f"{item.lng:.7f}",
                "hotspot_name": item.hotspot_name,
                "distance_band": item.distance_band,
                "primary_distance_m": f"{item.primary_distance_m:.1f}",
                "source_confidence": f"{item.source_confidence:.3f}",
                "kakao_url": item.kakao_url or "",
                "naver_url": item.naver_url or "",
                "poi_valid": "",
                "exclusion_reason": "",
            }
        )
    return buffer.getvalue()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument(
        "--hotspot",
        action="append",
        dest="hotspots",
        help="hotspot name (repeatable; defaults to Hongdae and Seongsu)",
    )
    parser.add_argument("--output", type=Path, help="write CSV instead of stdout")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="fail without writing CSV when any hotspot/distance band is short",
    )
    args = parser.parse_args(argv)

    hotspot_names = tuple(args.hotspots or EVAL_DEFAULT_HOTSPOT_NAMES)
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            result = select_candidates(session, hotspot_names)
    finally:
        engine.dispose()

    for hotspot_name, band, found, expected in result.shortages:
        print(
            f"shortage: hotspot={hotspot_name!r} band={band} "
            f"selected={found}/{expected}",
            file=sys.stderr,
        )
    if args.require_complete and result.shortages:
        return 1

    rendered = render_csv(result.candidates)
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
