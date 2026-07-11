from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Cafe, Hotspot, HotspotSnapshot
from scripts.run_eval import (
    EvaluationPoint,
    EvaluationReport,
    GroundTruth,
    evaluate,
    load_observations,
    main,
    render_markdown,
    spearman_rank_correlation,
    summarize,
)


NOW = datetime(2026, 7, 12, 3, 0, tzinfo=UTC)


def _cafe(cafe_id: int, *, lng: float) -> Cafe:
    return Cafe(
        id=cafe_id,
        overture_id=f"overture:{cafe_id}",
        source_release="2026-06-17.0",
        source_confidence=0.9,
        primary_category="cafe",
        name=f"카페 {cafe_id}",
        lat=37.0,
        lng=lng,
    )


def _seed_database(path: Path) -> str:
    database_url = f"sqlite+pysqlite:///{path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        left = Hotspot(
            id=1,
            area_cd="POI001",
            name="왼쪽",
            lat=37.0,
            lng=127.0,
            is_polled=True,
        )
        right = Hotspot(
            id=2,
            area_cd="POI002",
            name="오른쪽",
            lat=37.0,
            lng=127.02,
            is_polled=True,
        )
        ignored = Hotspot(
            id=3,
            area_cd="POI003",
            name="미폴링",
            lat=37.0,
            lng=127.0,
            is_polled=False,
        )
        session.add_all(
            [
                left,
                right,
                ignored,
                _cafe(1, lng=127.0),
                _cafe(2, lng=127.02),
                _cafe(3, lng=128.0),
            ]
        )
        session.flush()
        for hotspot_id, level in ((1, 1), (2, 4), (3, 4)):
            session.add(
                HotspotSnapshot(
                    hotspot_id=hotspot_id,
                    observed_at=NOW - timedelta(minutes=5),
                    fetched_at=NOW - timedelta(minutes=4),
                    congest_level=level,
                    congest_label=str(level),
                )
            )
        # This snapshot has an eligible source observation time but was not
        # fetched until after evaluation.  Using it would leak future knowledge.
        session.add(
            HotspotSnapshot(
                hotspot_id=1,
                observed_at=NOW - timedelta(minutes=2),
                fetched_at=NOW + timedelta(minutes=1),
                congest_level=4,
                congest_label="4",
            )
        )
        # These snapshots reverse the levels but are in the future relative to
        # the ground truth and therefore must not affect historical evaluation.
        for hotspot_id, level in ((1, 4), (2, 1)):
            session.add(
                HotspotSnapshot(
                    hotspot_id=hotspot_id,
                    observed_at=NOW + timedelta(minutes=5),
                    fetched_at=NOW + timedelta(minutes=6),
                    congest_level=level,
                    congest_label=str(level),
                )
            )
        session.commit()
    engine.dispose()
    return database_url


def test_csv_contract_counts_bad_rows_and_normalizes_to_utc(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,observed_level,slot\n"
        "1,2026-07-12T12:00:00+09:00,1,lunch\n"
        "2,2026-07-12T03:00:00,4,lunch\n"
        "3,2026-07-12T03:00:00Z,5,lunch\n",
        encoding="utf-8",
    )

    observations, total, invalid = load_observations(csv_path)

    assert total == 3
    assert invalid == 2
    assert len(observations) == 1
    assert observations[0].observed_at == NOW


def test_csv_missing_required_column_fails_the_contract(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text("cafe_id,observed_at\n1,2026-07-12T03:00:00Z\n")

    with pytest.raises(ValueError, match="observed_level"):
        load_observations(csv_path)


def test_spearman_uses_average_ranks_and_handles_undefined_cases() -> None:
    assert spearman_rank_correlation([1, 2, 2, 4], [1, 2, 3, 4]) == pytest.approx(
        0.948683298
    )
    assert spearman_rank_correlation([1], [1]) is None
    assert spearman_rank_correlation([1, 1], [2, 3]) is None
    with pytest.raises(ValueError, match="lengths differ"):
        spearman_rank_correlation([1], [1, 2])


def test_summary_macro_averages_timestamp_ranks_instead_of_pooling() -> None:
    later = NOW + timedelta(hours=1)
    points = (
        EvaluationPoint(GroundTruth(2, 1, NOW, 1, None), 1.0, 1, "covered", 1),
        EvaluationPoint(GroundTruth(3, 2, NOW, 2, None), 2.0, 2, "covered", 1),
        EvaluationPoint(GroundTruth(4, 1, later, 4, None), 3.0, 3, "covered", 1),
        EvaluationPoint(GroundTruth(5, 2, later, 3, None), 4.0, 4, "covered", 1),
    )

    metric = summarize(points)

    # Timestamp correlations are +1 and -1, hence macro mean 0.  Pooling all
    # four rows would incorrectly report +0.8.
    assert metric.spearman == pytest.approx(0.0)
    assert metric.adjacent_accuracy == 1.0


def test_evaluate_reconstructs_historical_snapshots_and_counts_outcomes(
    tmp_path: Path,
) -> None:
    database_url = _seed_database(tmp_path / "eval.db")
    engine = create_engine(database_url)
    truths = [
        GroundTruth(2, 1, NOW, 1, "same slot"),
        GroundTruth(3, 2, NOW, 4, "same slot"),
        GroundTruth(4, 3, NOW, 2, None),
        GroundTruth(5, 999, NOW, 2, None),
    ]
    with Session(engine) as session:
        report = evaluate(session, truths, total_rows=4, invalid_rows=0)
    engine.dispose()

    assert report.invalid_rows == 1
    assert report.uncovered_rows == 1
    assert [point.predicted_level for point in report.points] == [1, 4, None]
    assert [point.coverage for point in report.points] == [
        "covered",
        "covered",
        "uncovered",
    ]
    markdown = render_markdown(report)
    assert "| 2 | 1.000 | 1.000 |" in markdown
    assert "| 2026-07-12T03:00:00Z | same slot | 2 | 1.000 | 1.000 |" in markdown
    assert "- Uncovered: 1" in markdown
    assert "- Invalid: 1" in markdown


def test_render_markdown_splits_primary_distance_bands() -> None:
    truth = GroundTruth(2, 1, NOW, 2, None)
    covered = EvaluationPoint(truth, 2.0, 2, "covered", 100.0)
    fringe = EvaluationPoint(truth, 3.0, 3, "fringe", 900.0)
    report = EvaluationReport(2, 0, 0, (covered, fringe))

    markdown = render_markdown(report)

    assert "covered (≤ 600m) | 1 | N/A | 1.000" in markdown
    assert "fringe (> 600m, ≤ 1500m) | 1 | N/A | 1.000" in markdown
    assert report.points == (covered, fringe)


def test_cli_prints_by_default_and_only_writes_with_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_url = _seed_database(tmp_path / "eval.db")
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,observed_level,slot\n"
        "1,2026-07-12T03:00:00Z,1,slot-a\n"
        "2,2026-07-12T03:00:00Z,4,slot-a\n",
        encoding="utf-8",
    )

    assert main([str(csv_path), "--database-url", database_url]) == 0
    assert "# Cafe Crowd Evaluation" in capsys.readouterr().out
    assert list(tmp_path.glob("*.md")) == []

    output = tmp_path / "report.md"
    assert (
        main(
            [
                str(csv_path),
                "--database-url",
                database_url,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    assert output.read_text(encoding="utf-8").startswith("# Cafe Crowd Evaluation")
