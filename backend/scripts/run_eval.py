"""Evaluate historical cafe estimates against operator observations.

The evaluator never calls an external API.  For every observation timestamp it
reconstructs the latest hotspot state available at or before that instant and
runs the production scoring function with the production tuning constants.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import COVERED_M, R_MAX_M, SCORING_MODEL_VERSION
from app.database import create_db_engine
from app.models import Cafe, Hotspot, HotspotSnapshot
from app.scoring.engine import HotspotObservation, score_cafe


REQUIRED_COLUMNS = frozenset({"cafe_id", "observed_at", "observed_level"})


@dataclass(frozen=True, slots=True)
class GroundTruth:
    row_number: int
    cafe_id: int
    observed_at: datetime
    observed_level: int
    slot: str | None


@dataclass(frozen=True, slots=True)
class EvaluationPoint:
    truth: GroundTruth
    predicted_score: float | None
    predicted_level: int | None
    coverage: str
    primary_distance_m: float | None


@dataclass(frozen=True, slots=True)
class MetricSummary:
    observations: int
    spearman: float | None
    adjacent_accuracy: float | None


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    total_rows: int
    invalid_rows: int
    uncovered_rows: int
    points: tuple[EvaluationPoint, ...]


def _parse_iso_datetime(value: str | None) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("observed_at must be timezone-aware ISO8601")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware ISO8601")
    return parsed.astimezone(UTC)


def _parse_positive_int(value: str | None, *, field: str) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError(f"{field} must be an integer")
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{field} must be positive")
    return parsed


def load_observations(path: Path) -> tuple[list[GroundTruth], int, int]:
    """Load valid observations and count malformed rows.

    A malformed individual row is counted and excluded.  A missing required
    column makes the whole input contract unusable and raises ``ValueError``.
    """

    valid: list[GroundTruth] = []
    invalid = 0
    total = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = frozenset(reader.fieldnames or ())
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(f"missing required CSV columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            total += 1
            try:
                cafe_id = _parse_positive_int(row["cafe_id"], field="cafe_id")
                observed_at = _parse_iso_datetime(row["observed_at"])
                observed_level = _parse_positive_int(
                    row["observed_level"], field="observed_level"
                )
                if observed_level > 4:
                    raise ValueError("observed_level must be between 1 and 4")
                raw_slot = row.get("slot")
                slot = raw_slot.strip() if raw_slot and raw_slot.strip() else None
            except (KeyError, TypeError, ValueError):
                invalid += 1
                continue
            valid.append(
                GroundTruth(
                    row_number=row_number,
                    cafe_id=cafe_id,
                    observed_at=observed_at,
                    observed_level=observed_level,
                    slot=slot,
                )
            )
    return valid, total, invalid


def _as_utc(value: datetime) -> datetime:
    # SQLite returns naive values even for DateTime(timezone=True).  Stored
    # datetimes follow the application's UTC persistence contract.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _observations_at(
    session: Session, observed_at: datetime
) -> tuple[HotspotObservation, ...]:
    latest = (
        select(
            HotspotSnapshot.hotspot_id,
            func.max(HotspotSnapshot.observed_at).label("observed_at"),
        )
        .where(
            HotspotSnapshot.observed_at <= observed_at,
            HotspotSnapshot.fetched_at <= observed_at,
        )
        .group_by(HotspotSnapshot.hotspot_id)
        .subquery()
    )
    rows = session.execute(
        select(Hotspot, HotspotSnapshot)
        .join(latest, latest.c.hotspot_id == Hotspot.id)
        .join(
            HotspotSnapshot,
            (HotspotSnapshot.hotspot_id == latest.c.hotspot_id)
            & (HotspotSnapshot.observed_at == latest.c.observed_at),
        )
        .where(Hotspot.is_polled.is_(True))
        .order_by(Hotspot.id)
    ).all()
    return tuple(
        HotspotObservation(
            hotspot_id=hotspot.id,
            name=hotspot.name,
            lat=hotspot.lat,
            lng=hotspot.lng,
            level=snapshot.congest_level,
            observed_at=_as_utc(snapshot.observed_at),
        )
        for hotspot, snapshot in rows
    )


def evaluate(
    session: Session,
    truths: Sequence[GroundTruth],
    *,
    total_rows: int,
    invalid_rows: int,
) -> EvaluationReport:
    cafe_ids = sorted({truth.cafe_id for truth in truths})
    cafes = {
        cafe.id: cafe
        for cafe in session.scalars(select(Cafe).where(Cafe.id.in_(cafe_ids)))
    }
    missing_cafes = {cafe_id for cafe_id in cafe_ids if cafe_id not in cafes}
    invalid_rows += sum(truth.cafe_id in missing_cafes for truth in truths)

    observations_by_time = {
        observed_at: _observations_at(session, observed_at)
        for observed_at in sorted({truth.observed_at for truth in truths})
    }
    points: list[EvaluationPoint] = []
    uncovered = 0
    for truth in truths:
        cafe = cafes.get(truth.cafe_id)
        if cafe is None:
            continue
        estimate = score_cafe(
            cafe.lat,
            cafe.lng,
            observations_by_time[truth.observed_at],
            now=truth.observed_at,
        )
        if estimate.coverage == "uncovered":
            uncovered += 1
        points.append(
            EvaluationPoint(
                truth=truth,
                predicted_score=estimate.score,
                predicted_level=estimate.level,
                coverage=estimate.coverage,
                primary_distance_m=estimate.primary_distance_m,
            )
        )
    return EvaluationReport(
        total_rows=total_rows,
        invalid_rows=invalid_rows,
        uncovered_rows=uncovered,
        points=tuple(points),
    )


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = ((start + 1) + end) / 2.0
        for original_index, _ in ordered[start:end]:
            ranks[original_index] = average_rank
        start = end
    return ranks


def spearman_rank_correlation(
    predicted: Sequence[float], observed: Sequence[float]
) -> float | None:
    if len(predicted) != len(observed):
        raise ValueError("predicted and observed lengths differ")
    if len(predicted) < 2:
        return None
    predicted_ranks = _average_ranks(predicted)
    observed_ranks = _average_ranks(observed)
    predicted_mean = mean(predicted_ranks)
    observed_mean = mean(observed_ranks)
    numerator = sum(
        (left - predicted_mean) * (right - observed_mean)
        for left, right in zip(predicted_ranks, observed_ranks, strict=True)
    )
    left_sum = sum((value - predicted_mean) ** 2 for value in predicted_ranks)
    right_sum = sum((value - observed_mean) ** 2 for value in observed_ranks)
    denominator = (left_sum * right_sum) ** 0.5
    return None if denominator == 0 else numerator / denominator


def summarize(points: Iterable[EvaluationPoint]) -> MetricSummary:
    predicted = [
        point
        for point in points
        if point.predicted_score is not None and point.predicted_level is not None
    ]
    if not predicted:
        return MetricSummary(0, None, None)
    by_timestamp: dict[datetime, list[EvaluationPoint]] = defaultdict(list)
    for point in predicted:
        by_timestamp[point.truth.observed_at].append(point)
    timestamp_correlations: list[float] = []
    for observed_at in sorted(by_timestamp):
        timestamp_points = by_timestamp[observed_at]
        correlation = spearman_rank_correlation(
            [point.predicted_score for point in timestamp_points],
            [float(point.truth.observed_level) for point in timestamp_points],
        )
        if correlation is not None:
            timestamp_correlations.append(correlation)
    return MetricSummary(
        observations=len(predicted),
        spearman=(mean(timestamp_correlations) if timestamp_correlations else None),
        adjacent_accuracy=sum(
            abs(point.predicted_level - point.truth.observed_level) <= 1
            for point in predicted
        )
        / len(predicted),
    )


def _metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: EvaluationReport) -> str:
    valid_predictions = tuple(
        point for point in report.points if point.predicted_level is not None
    )
    overall = summarize(valid_predictions)
    lines = [
        "# Cafe Crowd Evaluation",
        "",
        f"- Model: `{SCORING_MODEL_VERSION}`",
        f"- Input rows: {report.total_rows}",
        f"- Valid predictions: {overall.observations}",
        f"- Uncovered: {report.uncovered_rows}",
        f"- Invalid: {report.invalid_rows}",
        "",
        "## Overall",
        "",
        "| Observations | Spearman | Adjacent accuracy |",
        "| ---: | ---: | ---: |",
        f"| {overall.observations} | {_metric(overall.spearman)} | "
        f"{_metric(overall.adjacent_accuracy)} |",
    ]

    by_slot: dict[tuple[datetime, str], list[EvaluationPoint]] = defaultdict(list)
    for point in valid_predictions:
        if point.truth.slot is not None:
            by_slot[(point.truth.observed_at, point.truth.slot)].append(point)
    if by_slot:
        lines.extend(
            [
                "",
                "## By slot",
                "",
                "| Observed at | Slot | Observations | Spearman | Adjacent accuracy |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for observed_at, slot in sorted(by_slot):
            metric = summarize(by_slot[(observed_at, slot)])
            timestamp = observed_at.isoformat().replace("+00:00", "Z")
            lines.append(
                f"| {timestamp} | {_escape_cell(slot)} | {metric.observations} | "
                f"{_metric(metric.spearman)} | {_metric(metric.adjacent_accuracy)} |"
            )

    lines.extend(
        [
            "",
            "## By primary distance",
            "",
            "| Band | Observations | Spearman | Adjacent accuracy |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    bands = (
        (
            f"covered (≤ {COVERED_M}m)",
            lambda point: point.primary_distance_m is not None
            and point.primary_distance_m <= COVERED_M,
        ),
        (
            f"fringe (> {COVERED_M}m, ≤ {R_MAX_M}m)",
            lambda point: point.primary_distance_m is not None
            and COVERED_M < point.primary_distance_m <= R_MAX_M,
        ),
    )
    for label, contains in bands:
        metric = summarize(
            point for point in valid_predictions if contains(point)
        )
        lines.append(
            f"| {label} | {metric.observations} | {_metric(metric.spearman)} | "
            f"{_metric(metric.adjacent_accuracy)} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path, help="ground-truth CSV")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument("--output", type=Path, help="write Markdown report")
    args = parser.parse_args(argv)

    try:
        truths, total, invalid = load_observations(args.observations)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        parser.error(str(exc))

    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = evaluate(
                session,
                truths,
                total_rows=total,
                invalid_rows=invalid,
            )
    finally:
        engine.dispose()
    markdown = render_markdown(report)
    if args.output is None:
        print(markdown, end="")
    else:
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
