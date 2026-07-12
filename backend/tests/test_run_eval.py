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
    ReliabilitySummary,
    evaluate,
    load_observations,
    main,
    quadratic_weighted_cohen_kappa,
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
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,observed_venue_level,"
        "pedestrians_per_min,flow_obstruction,observer_notes\n"
        "1,2026-07-12T12:00:00+09:00,lunch,a,primary,1,,5,none,\n"
        "2,2026-07-12T03:00:00,lunch,a,primary,4,2,31,none,\n"
        "3,2026-07-12T03:00:00Z,lunch,a,primary,5,3,31,none,\n",
        encoding="utf-8",
    )

    observations, total, invalid = load_observations(csv_path)

    assert total == 3
    assert invalid == 2
    assert len(observations) == 1
    assert observations[0].observed_at == NOW
    assert observations[0].observed_area_level == 1
    assert observations[0].observed_venue_level is None
    assert observations[0].pedestrians_per_min == 5
    assert observations[0].flow_obstruction == "none"


def test_csv_missing_required_column_fails_the_contract(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,"
        "flow_obstruction\n"
        "1,2026-07-12T03:00:00Z,lunch,a,primary,1,5,none\n"
    )

    with pytest.raises(ValueError, match="observer_notes"):
        load_observations(csv_path)


def test_csv_requires_observer_contract_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observed_area_level,pedestrians_per_min,"
        "flow_obstruction,observer_notes\n"
        "1,2026-07-12T03:00:00Z,lunch,1,5,none,\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="observation_role, observer_id"):
        load_observations(csv_path)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            "1,2026-07-12T03:00:00Z,lunch,a,primary,1,5,none,\n"
            "1,2026-07-12T03:01:00Z,lunch,a,reliability,1,5,none,\n",
            "duplicate observer/cafe/slot",
        ),
        (
            "1,2026-07-12T03:00:00Z,lunch,a,primary,1,5,none,\n"
            "1,2026-07-12T03:01:00Z,lunch,b,primary,1,5,none,\n",
            "exactly one primary",
        ),
        (
            "1,2026-07-12T03:00:00Z,lunch,b,reliability,1,5,none,\n",
            "exactly one primary",
        ),
    ],
)
def test_csv_observer_structure_fails_closed(
    tmp_path: Path, rows: str, message: str
) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,flow_obstruction,"
        "observer_notes\n"
        + rows,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_observations(csv_path)


def test_csv_loads_one_independent_reliability_observation(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,flow_obstruction,"
        "observer_notes\n"
        "1,2026-07-12T03:00:00Z,lunch,a,primary,1,5,none,\n"
        "1,2026-07-12T03:01:00Z,lunch,b,reliability,2,10,none,\n",
        encoding="utf-8",
    )

    observations, total, invalid = load_observations(csv_path)

    assert total == 2
    assert invalid == 0
    assert [row.observer_id for row in observations] == ["a", "b"]
    assert [row.observation_role for row in observations] == [
        "primary",
        "reliability",
    ]


@pytest.mark.parametrize(
    ("observer_id", "observation_role"),
    [("", "primary"), ("a", "secondary"), ("a", "")],
)
def test_csv_rejects_invalid_observer_fields(
    tmp_path: Path, observer_id: str, observation_role: str
) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,flow_obstruction,"
        "observer_notes\n"
        f"1,2026-07-12T03:00:00Z,lunch,{observer_id},{observation_role},"
        "1,5,none,\n",
        encoding="utf-8",
    )

    observations, total, invalid = load_observations(csv_path)

    assert observations == []
    assert total == 1
    assert invalid == 1


def test_csv_rejects_blank_slot_and_invalid_optional_venue_level(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,observed_venue_level,"
        "pedestrians_per_min,flow_obstruction,observer_notes\n"
        "1,2026-07-12T03:00:00Z,,a,primary,1,2,5,none,\n"
        "2,2026-07-12T03:00:00Z,lunch,a,primary,4,5,31,none,\n",
        encoding="utf-8",
    )

    observations, total, invalid = load_observations(csv_path)

    assert observations == []
    assert total == 2
    assert invalid == 2


def test_csv_allows_missing_optional_venue_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,"
        "flow_obstruction,observer_notes\n"
        "1,2026-07-12T03:00:00Z,lunch,a,primary,1,5,none,\n",
        encoding="utf-8",
    )

    observations, total, invalid = load_observations(csv_path)

    assert total == 1
    assert invalid == 0
    assert observations[0].observed_venue_level is None


@pytest.mark.parametrize(
    ("pedestrians_per_min", "expected_level"),
    [("5", 1), ("15", 2), ("30", 3), ("30.1", 4)],
)
def test_csv_derives_area_level_at_threshold_boundaries(
    tmp_path: Path, pedestrians_per_min: str, expected_level: int
) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,"
        "flow_obstruction,observer_notes\n"
        f"1,2026-07-12T03:00:00Z,lunch,a,primary,{expected_level},"
        f"{pedestrians_per_min},none,\n",
        encoding="utf-8",
    )

    observations, _, invalid = load_observations(csv_path)

    assert invalid == 0
    assert observations[0].observed_area_level == expected_level


@pytest.mark.parametrize(
    ("flow_obstruction", "expected_level", "notes"),
    [
        ("repeated_avoidance", 3, "people repeatedly stepped aside"),
        ("blocked", 4, "flow stopped"),
    ],
)
def test_csv_flow_obstruction_overrides_raw_level(
    tmp_path: Path, flow_obstruction: str, expected_level: int, notes: str
) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,"
        "flow_obstruction,observer_notes\n"
        f"1,2026-07-12T03:00:00Z,lunch,a,primary,{expected_level},1,"
        f"{flow_obstruction},{notes}\n",
        encoding="utf-8",
    )

    observations, _, invalid = load_observations(csv_path)

    assert invalid == 0
    assert observations[0].flow_obstruction == flow_obstruction
    assert observations[0].observer_notes == notes


def test_csv_rejects_area_level_mismatch(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,"
        "flow_obstruction,observer_notes\n"
        "1,2026-07-12T03:00:00Z,lunch,a,primary,4,5,none,\n",
        encoding="utf-8",
    )

    observations, _, invalid = load_observations(csv_path)

    assert observations == []
    assert invalid == 1


@pytest.mark.parametrize("value", ["NaN", "inf", "-1"])
def test_csv_rejects_invalid_pedestrian_counts(tmp_path: Path, value: str) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,"
        "flow_obstruction,observer_notes\n"
        f"1,2026-07-12T03:00:00Z,lunch,a,primary,1,{value},none,\n",
        encoding="utf-8",
    )

    observations, _, invalid = load_observations(csv_path)

    assert observations == []
    assert invalid == 1


def test_csv_requires_notes_for_obstruction(tmp_path: Path) -> None:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,pedestrians_per_min,"
        "flow_obstruction,observer_notes\n"
        "1,2026-07-12T03:00:00Z,lunch,a,primary,3,1,repeated_avoidance\n",
        encoding="utf-8",
    )

    observations, _, invalid = load_observations(csv_path)

    assert observations == []
    assert invalid == 1


def test_spearman_uses_average_ranks_and_handles_undefined_cases() -> None:
    assert spearman_rank_correlation([1, 2, 2, 4], [1, 2, 3, 4]) == pytest.approx(
        0.948683298
    )
    assert spearman_rank_correlation([1], [1]) is None
    assert spearman_rank_correlation([1, 1], [2, 3]) is None
    with pytest.raises(ValueError, match="lengths differ"):
        spearman_rank_correlation([1], [1, 2])


def test_quadratic_weighted_kappa_is_deterministic_and_fail_closed() -> None:
    assert quadratic_weighted_cohen_kappa([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert quadratic_weighted_cohen_kappa([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert quadratic_weighted_cohen_kappa([1], [1]) is None
    assert quadratic_weighted_cohen_kappa([1, 1], [1, 2]) is None
    with pytest.raises(ValueError, match="lengths differ"):
        quadratic_weighted_cohen_kappa([1], [1, 2])
    with pytest.raises(ValueError, match="between 1 and 4"):
        quadratic_weighted_cohen_kappa([1, 5], [1, 2])


def test_summary_macro_averages_slot_ranks_instead_of_timestamps() -> None:
    later = NOW + timedelta(hours=1)
    points = (
        EvaluationPoint(GroundTruth(2, 1, NOW, "lunch", 1), 1.0, 1, "covered", 1),
        EvaluationPoint(GroundTruth(3, 2, later, "lunch", 2), 2.0, 2, "covered", 1),
        EvaluationPoint(GroundTruth(4, 1, NOW, "dinner", 4), 3.0, 3, "covered", 1),
        EvaluationPoint(GroundTruth(5, 2, later, "dinner", 3), 4.0, 4, "covered", 1),
    )

    metric = summarize(points)

    # Slot correlations are +1 and -1, hence macro mean 0.  Pooling all
    # four rows would incorrectly report +0.8.
    assert metric.spearman == pytest.approx(0.0)
    assert metric.adjacent_accuracy == 1.0


def test_summary_keeps_optional_venue_utility_separate() -> None:
    points = (
        EvaluationPoint(
            GroundTruth(2, 1, NOW, "lunch", 1, 4),
            1.0,
            1,
            "covered",
            1,
        ),
        EvaluationPoint(
            GroundTruth(3, 2, NOW, "lunch", 4, 1),
            4.0,
            4,
            "covered",
            1,
        ),
        EvaluationPoint(
            GroundTruth(4, 3, NOW, "lunch", 2),
            2.0,
            2,
            "covered",
            1,
        ),
    )

    area = summarize(points)
    venue = summarize(points, target="venue")

    assert area.observations == 3
    assert area.spearman == pytest.approx(1.0)
    assert area.adjacent_accuracy == 1.0
    assert venue.observations == 2
    assert venue.spearman == pytest.approx(-1.0)
    assert venue.adjacent_accuracy == 0.0


def test_evaluate_reconstructs_historical_snapshots_and_counts_outcomes(
    tmp_path: Path,
) -> None:
    database_url = _seed_database(tmp_path / "eval.db")
    engine = create_engine(database_url)
    truths = [
        GroundTruth(2, 1, NOW, "same slot", 1, 2),
        GroundTruth(3, 2, NOW, "same slot", 4, 3),
        GroundTruth(4, 3, NOW, "same slot", 2),
        GroundTruth(5, 999, NOW, "same slot", 2),
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
    assert "| same slot | 2 | 1.000 | 1.000 |" in markdown
    assert "## Primary surrounding-area metrics" in markdown
    assert "## Optional venue utility metrics" in markdown
    assert "Validation contract: area level is derived" in markdown
    assert "| 2 | 1.000 | 1.000 |" in markdown
    assert "- Uncovered: 1" in markdown
    assert "- Invalid: 1" in markdown


def test_evaluate_excludes_reliability_rows_from_engine_metrics(
    tmp_path: Path,
) -> None:
    database_url = _seed_database(tmp_path / "eval.db")
    engine = create_engine(database_url)
    truths = [
        GroundTruth(2, 1, NOW, "same slot", 1, observer_id="a"),
        GroundTruth(
            3,
            1,
            NOW,
            "same slot",
            2,
            observer_id="b",
            observation_role="reliability",
        ),
        GroundTruth(4, 2, NOW, "same slot", 4, observer_id="a"),
        GroundTruth(
            5,
            2,
            NOW,
            "same slot",
            3,
            observer_id="b",
            observation_role="reliability",
        ),
    ]
    with Session(engine) as session:
        report = evaluate(session, truths, total_rows=4, invalid_rows=0)
    engine.dispose()

    assert len(report.points) == 2
    assert report.reliability.pairs == 2
    assert report.reliability.quadratic_weighted_kappa == pytest.approx(0.6)
    markdown = render_markdown(report)
    assert "## Inter-observer reliability" in markdown
    assert "| 2 | 0.600 |" in markdown
    assert "primary observations only" in markdown


def test_render_markdown_splits_primary_distance_bands() -> None:
    truth = GroundTruth(2, 1, NOW, "lunch", 2)
    covered = EvaluationPoint(truth, 2.0, 2, "covered", 100.0)
    fringe = EvaluationPoint(truth, 3.0, 3, "fringe", 900.0)
    report = EvaluationReport(2, 0, 0, (covered, fringe))

    markdown = render_markdown(report)

    assert "covered (≤ 600m) | 1 | N/A | 1.000" in markdown
    assert "fringe (> 600m, ≤ 1500m) | 1 | N/A | 1.000" in markdown
    assert report.points == (covered, fringe)


def test_render_markdown_reports_na_reliability_without_enough_variance() -> None:
    report = EvaluationReport(
        2,
        0,
        0,
        (),
        ReliabilitySummary(2, None),
    )

    markdown = render_markdown(report)

    assert "| 2 | N/A |" in markdown


def test_cli_prints_by_default_and_only_writes_with_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_url = _seed_database(tmp_path / "eval.db")
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "cafe_id,observed_at,slot,observer_id,observation_role,"
        "observed_area_level,observed_venue_level,"
        "pedestrians_per_min,flow_obstruction,observer_notes\n"
        "1,2026-07-12T03:00:00Z,slot-a,a,primary,1,2,5,none,\n"
        "2,2026-07-12T03:00:00Z,slot-a,a,primary,4,3,31,none,\n",
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
