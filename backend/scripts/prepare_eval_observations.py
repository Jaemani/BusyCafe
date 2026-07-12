"""Prepare a fail-closed Phase 6 field-observation worksheet."""

from __future__ import annotations

import argparse
import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


CANDIDATE_REQUIRED_COLUMNS = frozenset(
    {
        "cafe_id",
        "name",
        "road_address",
        "lat",
        "lng",
        "hotspot_name",
        "distance_band",
        "poi_valid",
        "exclusion_reason",
    }
)
OUTPUT_FIELDS = (
    "cafe_id",
    "name",
    "road_address",
    "lat",
    "lng",
    "hotspot_name",
    "distance_band",
    "observer_id",
    "observation_role",
    "observed_at",
    "slot",
    "observed_area_level",
    "observed_venue_level",
    "pedestrians_per_min",
    "flow_obstruction",
    "observer_notes",
)
DISTANCE_BANDS = ("near", "mid", "fringe")


@dataclass(frozen=True, slots=True)
class ReviewedCandidate:
    cafe_id: int
    name: str
    road_address: str
    lat: str
    lng: str
    hotspot_name: str
    distance_band: str
    poi_valid: bool


@dataclass(frozen=True, slots=True)
class FieldSession:
    hotspot_name: str
    slot: str


def _parse_cafe_id(value: str | None) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError("cafe_id must be a positive integer")
    parsed = int(value)
    if parsed < 1:
        raise ValueError("cafe_id must be a positive integer")
    return parsed


def _parse_poi_valid(value: str | None) -> bool:
    if not isinstance(value, str):
        raise ValueError("poi_valid must be true or false")
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("poi_valid must be true or false")


def load_reviewed_candidates(path: Path) -> tuple[ReviewedCandidate, ...]:
    """Load candidates only when every POI has an explicit review decision."""

    candidates: list[ReviewedCandidate] = []
    seen_cafe_ids: set[int] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = frozenset(reader.fieldnames or ())
        missing = sorted(CANDIDATE_REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(
                f"missing required candidate columns: {', '.join(missing)}"
            )
        for row_number, row in enumerate(reader, start=2):
            try:
                cafe_id = _parse_cafe_id(row.get("cafe_id"))
                poi_valid = _parse_poi_valid(row.get("poi_valid"))
                metadata = {
                    field: row.get(field)
                    for field in (
                        "name",
                        "road_address",
                        "lat",
                        "lng",
                        "hotspot_name",
                        "distance_band",
                    )
                }
                for field in (
                    "name",
                    "lat",
                    "lng",
                    "hotspot_name",
                    "distance_band",
                ):
                    value = metadata[field]
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"{field} must be nonblank")
                exclusion_reason = row.get("exclusion_reason")
                if not poi_valid and (
                    not isinstance(exclusion_reason, str)
                    or not exclusion_reason.strip()
                ):
                    raise ValueError(
                        "exclusion_reason is required when poi_valid is false"
                    )
                if cafe_id in seen_cafe_ids:
                    raise ValueError(f"duplicate cafe_id: {cafe_id}")
            except ValueError as exc:
                raise ValueError(f"candidate row {row_number}: {exc}") from exc
            seen_cafe_ids.add(cafe_id)
            candidates.append(
                ReviewedCandidate(
                    cafe_id=cafe_id,
                    name=metadata["name"] or "",
                    road_address=metadata["road_address"] or "",
                    lat=metadata["lat"] or "",
                    lng=metadata["lng"] or "",
                    hotspot_name=metadata["hotspot_name"] or "",
                    distance_band=metadata["distance_band"] or "",
                    poi_valid=poi_valid,
                )
            )
    if not any(candidate.poi_valid for candidate in candidates):
        raise ValueError("candidate CSV has no valid reviewed POIs")
    return tuple(candidates)


def parse_sessions(values: Sequence[str]) -> tuple[FieldSession, ...]:
    if not values:
        raise ValueError("at least one --session is required")
    sessions: list[FieldSession] = []
    for value in values:
        hotspot_name, separator, slot = value.partition("=")
        hotspot_name = hotspot_name.strip()
        slot = slot.strip()
        if not separator or not hotspot_name or not slot:
            raise ValueError(
                "session must use nonblank HOTSPOT_NAME=SLOT_ID"
            )
        sessions.append(FieldSession(hotspot_name, slot))
    if len({session.slot for session in sessions}) != len(sessions):
        raise ValueError("slot IDs must be unique")
    return tuple(sessions)


def parse_observers(values: Sequence[str]) -> tuple[str, str]:
    observers = tuple(value.strip() for value in values)
    if len(observers) != 2:
        raise ValueError("exactly two --observer values are required")
    if not all(observers):
        raise ValueError("observer IDs must be nonblank")
    if len(set(observers)) != 2:
        raise ValueError("observer IDs must be unique")
    return observers


def _ordered_session_candidates(
    candidates: Sequence[ReviewedCandidate], session: FieldSession
) -> tuple[ReviewedCandidate, ...]:
    session_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.poi_valid
        and candidate.hotspot_name.strip() == session.hotspot_name
    )
    if not session_candidates:
        raise ValueError(
            f"session hotspot has no valid candidates: {session.hotspot_name}"
        )
    unknown_bands = sorted(
        {candidate.distance_band for candidate in session_candidates}
        - set(DISTANCE_BANDS)
    )
    if unknown_bands:
        raise ValueError(
            "session contains unsupported distance bands: "
            + ", ".join(unknown_bands)
        )
    return tuple(
        sorted(
            session_candidates,
            key=lambda candidate: (
                DISTANCE_BANDS.index(candidate.distance_band),
                candidate.cafe_id,
            ),
        )
    )


def _reliability_candidates(
    candidates: Sequence[ReviewedCandidate], *, session_index: int
) -> frozenset[int]:
    by_band = {
        band: tuple(
            candidate
            for candidate in candidates
            if candidate.distance_band == band
        )
        for band in DISTANCE_BANDS
    }
    missing_bands = [band for band, rows in by_band.items() if not rows]
    if missing_bands:
        raise ValueError(
            "reliability overlap requires every distance band: "
            + ", ".join(missing_bands)
        )
    rotated_band = DISTANCE_BANDS[session_index % len(DISTANCE_BANDS)]
    if len(by_band[rotated_band]) < 2:
        raise ValueError(
            "reliability overlap requires two candidates in rotated band: "
            f"{rotated_band}"
        )
    selected = {
        rows[session_index % len(rows)].cafe_id for rows in by_band.values()
    }
    rotated_rows = by_band[rotated_band]
    selected.add(rotated_rows[(session_index + 1) % len(rotated_rows)].cafe_id)
    if len(selected) != 4:
        raise ValueError("reliability overlap must select four distinct candidates")
    return frozenset(selected)


def render_worksheet(
    candidates: Sequence[ReviewedCandidate],
    sessions: Sequence[FieldSession],
    observers: tuple[str, str],
) -> str:
    if not sessions:
        raise ValueError("at least one session is required")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for session_index, session in enumerate(sessions):
        session_candidates = _ordered_session_candidates(candidates, session)
        reliability_ids = _reliability_candidates(
            session_candidates, session_index=session_index
        )
        for candidate_index, candidate in enumerate(session_candidates):
            primary_observer_index = (candidate_index + session_index) % 2
            primary_observer = observers[primary_observer_index]
            common = {
                "cafe_id": candidate.cafe_id,
                "name": candidate.name,
                "road_address": candidate.road_address,
                "lat": candidate.lat,
                "lng": candidate.lng,
                "hotspot_name": candidate.hotspot_name,
                "distance_band": candidate.distance_band,
                "observed_at": "",
                "slot": session.slot,
                "observed_area_level": "",
                "observed_venue_level": "",
                "pedestrians_per_min": "",
                "flow_obstruction": "",
                "observer_notes": "",
            }
            writer.writerow(
                {
                    **common,
                    "observer_id": primary_observer,
                    "observation_role": "primary",
                }
            )
            if candidate.cafe_id in reliability_ids:
                writer.writerow(
                    {
                        **common,
                        "observer_id": observers[1 - primary_observer_index],
                        "observation_role": "reliability",
                    }
                )
    return buffer.getvalue()


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(content)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path, help="reviewed candidate CSV")
    parser.add_argument(
        "--session",
        action="append",
        required=True,
        help="HOTSPOT_NAME=SLOT_ID (repeatable)",
    )
    parser.add_argument(
        "--observer",
        action="append",
        required=True,
        help="observer ID (exactly two unique values required)",
    )
    parser.add_argument("--output", type=Path, help="write CSV instead of stdout")
    args = parser.parse_args(argv)

    try:
        candidates = load_reviewed_candidates(args.candidates)
        sessions = parse_sessions(args.session)
        observers = parse_observers(args.observer)
        rendered = render_worksheet(candidates, sessions, observers)
        if args.output is None:
            print(rendered, end="")
        else:
            _write_new(args.output, rendered)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
