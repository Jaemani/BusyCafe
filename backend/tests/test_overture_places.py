from __future__ import annotations

from math import inf, nextafter
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import SEOUL_BBOX
from app.ingest.overture_places import (
    OvertureCafeRecord,
    OvertureIngestError,
    build_confidence_report,
    format_confidence_report,
    overture_seed_value_equal,
    parse_overture_row,
    seed_overture_cafes,
    summarize_numeric_deltas,
)
from app.models import Base, Cafe


def record(identifier: str = "overture:test-1", **overrides: object) -> OvertureCafeRecord:
    values: dict[str, object] = {
        "overture_id": identifier,
        "name": "테스트 카페",
        "lat": 37.55,
        "lng": 126.98,
        "primary_category": "cafe",
        "confidence": 0.9,
        "road_address": "서울시 테스트구 1",
        "phone": "02-123-4567",
        "website": "https://example.test",
        "sources": [{"dataset": "test"}],
    }
    values.update(overrides)
    return OvertureCafeRecord(**values)  # type: ignore[arg-type]


@pytest.fixture
def engine():
    db_engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    yield db_engine
    db_engine.dispose()


def test_parse_overture_row_preserves_source_and_normalizes_optional_text() -> None:
    parsed = parse_overture_row(
        {
            "overture_id": "  overture:1 ",
            "name": "  카페  ",
            "lat": 37.55,
            "lng": 126.98,
            "primary_category": "cafe",
            "confidence": 0.8,
            "road_address": " ",
            "phone": None,
            "website": "https://example.test",
            "sources_json": '[{"dataset":"meta"}]',
        }
    )

    assert parsed.overture_id == "overture:1"
    assert parsed.name == "카페"
    assert parsed.road_address is None
    assert parsed.sources == [{"dataset": "meta"}]


@pytest.mark.parametrize(
    "row",
    [
        {"overture_id": "x"},
        {
            "overture_id": "x",
            "name": "x",
            "lat": 100,
            "lng": 127,
            "primary_category": "cafe",
            "confidence": 0.8,
        },
        {
            "overture_id": "x",
            "name": "x",
            "lat": 37,
            "lng": 127,
            "primary_category": "cafe",
            "confidence": 1.1,
        },
    ],
)
def test_parse_overture_row_rejects_invalid_data(row: dict[str, object]) -> None:
    with pytest.raises(OvertureIngestError):
        parse_overture_row(row)


def test_seed_is_idempotent_and_deactivates_missing_records(engine) -> None:
    with Session(engine) as session:
        first = seed_overture_cafes(
            session,
            [record("overture:1"), record("overture:2", name="둘")],
            release="2026-06-17.0",
            scope_bbox=SEOUL_BBOX,
        )
        second = seed_overture_cafes(
            session,
            [record("overture:1"), record("overture:2", name="둘")],
            release="2026-06-17.0",
            scope_bbox=SEOUL_BBOX,
        )
        third = seed_overture_cafes(
            session,
            [record("overture:1", name="바뀐 이름")],
            release="2026-07-01.0",
            scope_bbox=SEOUL_BBOX,
        )

        assert first.inserted_count == 2
        assert second.unchanged_count == 2
        assert third.updated_count == 1
        assert third.deactivated_count == 1
        assert third.changed_field_counts == (("name", 1), ("source_release", 1))
        assert session.scalar(select(func.count()).select_from(Cafe)) == 2
        assert session.scalar(select(Cafe).where(Cafe.overture_id == "overture:1")).name == "바뀐 이름"
        assert session.scalar(select(Cafe).where(Cafe.overture_id == "overture:2")).active is False


def test_dry_run_has_no_database_effect_and_duplicate_ids_fail(engine) -> None:
    with Session(engine) as session:
        report = seed_overture_cafes(
            session,
            [record()],
            release="2026-06-17.0",
            scope_bbox=SEOUL_BBOX,
            dry_run=True,
        )
        assert report.inserted_count == 1
        assert session.scalar(select(func.count()).select_from(Cafe)) == 0
        with pytest.raises(OvertureIngestError, match="duplicate"):
            seed_overture_cafes(
                session,
                [record(), record()],
                release="2026-06-17.0",
                scope_bbox=SEOUL_BBOX,
            )


def test_seed_dry_run_aggregates_changed_fields_without_values_or_ids(engine) -> None:
    with Session(engine) as session:
        seed_overture_cafes(
            session,
            [record("overture:1"), record("overture:2")],
            release="old-release",
            scope_bbox=SEOUL_BBOX,
        )

        report = seed_overture_cafes(
            session,
            [
                record("overture:1", phone="02-000-0001"),
                record("overture:2", phone="02-000-0002"),
            ],
            release="new-release",
            scope_bbox=SEOUL_BBOX,
            dry_run=True,
        )

        assert report.updated_count == 2
        assert report.changed_field_counts == (("phone", 2), ("source_release", 2))
        assert all("overture:" not in field for field, _ in report.changed_field_counts)


def test_summarize_numeric_deltas_is_deterministic_and_linear() -> None:
    assert summarize_numeric_deltas([]) is None

    summary = summarize_numeric_deltas([4.0, 0.0, 2.0, 1.0, 3.0])

    assert summary is not None
    assert summary.count == 5
    assert summary.minimum == 0.0
    assert summary.p50 == 2.0
    assert summary.p95 == pytest.approx(3.8)
    assert summary.maximum == 4.0


def test_seed_float_comparison_uses_absolute_tolerance_only_for_numeric_fields() -> None:
    tolerance = 1e-12

    assert overture_seed_value_equal(
        "lat", 0.0, tolerance, coordinate_abs_tol_deg=tolerance
    )
    assert not overture_seed_value_equal(
        "lng", 0.0, nextafter(tolerance, inf), coordinate_abs_tol_deg=tolerance
    )
    assert overture_seed_value_equal(
        "source_confidence", 0.0, tolerance, confidence_abs_tol=tolerance
    )
    assert not overture_seed_value_equal(
        "source_confidence",
        0.0,
        nextafter(tolerance, inf),
        confidence_abs_tol=tolerance,
    )
    assert not overture_seed_value_equal("name", "Cafe", "cafe")


def test_seed_ignores_float_round_trip_jitter(engine) -> None:
    original = record("overture:1")
    jittered = record(
        "overture:1",
        lat=nextafter(original.lat, inf),
        lng=nextafter(original.lng, inf),
        confidence=nextafter(original.confidence, inf),
    )
    with Session(engine) as session:
        seed_overture_cafes(
            session,
            [original],
            release="same-release",
            scope_bbox=SEOUL_BBOX,
        )
        report = seed_overture_cafes(
            session,
            [jittered],
            release="same-release",
            scope_bbox=SEOUL_BBOX,
            dry_run=True,
        )

        assert report.unchanged_count == 1
        assert report.updated_count == 0
        assert report.changed_field_counts == ()
        assert report.coordinate_delta_m is None
        assert report.confidence_abs_delta is None


def test_seed_dry_run_summarizes_coordinate_and_confidence_deltas(engine) -> None:
    original = record("overture:1")
    changed = record(
        "overture:1",
        lat=original.lat + 0.001,
        confidence=original.confidence - 0.2,
    )
    with Session(engine) as session:
        seed_overture_cafes(
            session,
            [original],
            release="same-release",
            scope_bbox=SEOUL_BBOX,
        )
        report = seed_overture_cafes(
            session,
            [changed],
            release="same-release",
            scope_bbox=SEOUL_BBOX,
            dry_run=True,
        )

        assert report.coordinate_delta_m is not None
        assert report.coordinate_delta_m.count == 1
        assert report.coordinate_delta_m.minimum == report.coordinate_delta_m.maximum
        assert report.coordinate_delta_m.maximum == pytest.approx(111.2, abs=0.2)
        assert report.confidence_abs_delta is not None
        assert report.confidence_abs_delta.count == 1
        assert report.confidence_abs_delta.p50 == pytest.approx(0.2)


def test_seed_deactivation_is_limited_to_explicit_scope(engine) -> None:
    scope_a = (126.90, 37.50, 127.00, 37.60)
    scope_b = (127.10, 37.50, 127.20, 37.60)
    record_a = record("overture:a", lng=126.95, lat=37.55)
    record_b = record("overture:b", lng=127.15, lat=37.55)

    with Session(engine) as session:
        seed_overture_cafes(
            session, [record_a], release="2026-06-17.0", scope_bbox=scope_a
        )
        report = seed_overture_cafes(
            session, [record_b], release="2026-06-17.0", scope_bbox=scope_b
        )

        assert report.deactivated_count == 0
        assert session.scalar(
            select(Cafe.active).where(Cafe.overture_id == "overture:a")
        ) is True
        assert session.scalar(
            select(Cafe.active).where(Cafe.overture_id == "overture:b")
        ) is True


def test_seed_rejects_record_outside_scope_before_database_mutation(engine) -> None:
    scope = (126.90, 37.50, 127.00, 37.60)

    with Session(engine) as session:
        with pytest.raises(OvertureIngestError, match="outside seed scope"):
            seed_overture_cafes(
                session,
                [
                    record("overture:inside", lng=126.95, lat=37.55),
                    record("overture:outside", lng=127.01, lat=37.55),
                ],
                release="2026-06-17.0",
                scope_bbox=scope,
            )

        assert session.scalar(select(func.count()).select_from(Cafe)) == 0


def test_seed_scope_edges_are_inclusive(engine) -> None:
    scope = (126.90, 37.50, 127.00, 37.60)

    with Session(engine) as session:
        report = seed_overture_cafes(
            session,
            [record("overture:edge", lng=127.00, lat=37.60)],
            release="2026-06-17.0",
            scope_bbox=scope,
        )

        assert report.inserted_count == 1


def test_confidence_report_buckets_pass_count_and_categories() -> None:
    # confidence=0.50 pins the observed floor to the report's lowest edge, so
    # this fixture isolates bucketing/category counting from the separate
    # cache_pre_filtered heuristic (covered on its own below).
    records = [
        record("overture:0", confidence=0.50, primary_category="bubble_tea"),
        record("overture:1", confidence=0.55, primary_category="cafe"),
        record("overture:2", confidence=0.81, primary_category="cafe"),
        record("overture:3", confidence=0.81, primary_category="coffee_shop"),
        record("overture:4", confidence=0.99, primary_category="tea_room"),
    ]

    report = build_confidence_report(records, threshold=0.80)

    assert report.total_count == 5
    assert report.passing_count == 3
    assert report.observed_min_confidence == 0.50
    assert report.cache_pre_filtered is False
    assert report.below_range_count == 0
    assert report.category_counts == {
        "bubble_tea": 1,
        "cafe": 2,
        "coffee_shop": 1,
        "tea_room": 1,
    }

    bucket_by_range = {(bucket.lower, bucket.upper): bucket for bucket in report.buckets}
    assert bucket_by_range[(0.50, 0.55)].count == 1
    assert bucket_by_range[(0.55, 0.60)].count == 1
    assert bucket_by_range[(0.55, 0.60)].cache_filtered is False
    assert bucket_by_range[(0.80, 0.85)].count == 2
    assert bucket_by_range[(0.95, 1.00)].count == 1
    assert sum(bucket.count for bucket in report.buckets) == 5


def test_confidence_report_flags_buckets_below_the_download_time_floor() -> None:
    """A cache produced by cache_seoul_extract can never contain records below

    whatever --min-confidence was used at download time. build_confidence_report
    must say so instead of reporting a misleading zero for those buckets.
    """

    records = [
        record("overture:1", confidence=0.81),
        record("overture:2", confidence=0.93),
    ]

    report = build_confidence_report(records, threshold=0.80)

    assert report.cache_pre_filtered is True
    assert report.observed_min_confidence == 0.81

    below_floor = [bucket for bucket in report.buckets if bucket.upper <= 0.80]
    assert below_floor  # sanity: buckets 0.50-0.80 exist
    assert all(bucket.cache_filtered and bucket.count == 0 for bucket in below_floor)

    at_or_above_floor = [bucket for bucket in report.buckets if bucket.lower >= 0.80]
    assert at_or_above_floor
    assert all(not bucket.cache_filtered for bucket in at_or_above_floor)


def test_confidence_report_no_filter_flag_when_full_range_is_present() -> None:
    records = [record("overture:1", confidence=0.50), record("overture:2", confidence=0.99)]

    report = build_confidence_report(records, threshold=0.80)

    assert report.cache_pre_filtered is False
    assert all(not bucket.cache_filtered for bucket in report.buckets)


def test_confidence_report_handles_below_range_and_exact_boundary_values() -> None:
    records = [
        record("overture:1", confidence=0.45),  # below the 0.50 report floor
        record("overture:2", confidence=1.00),  # exact upper edge
    ]

    report = build_confidence_report(records, threshold=0.80)

    assert report.below_range_count == 1
    last_bucket = report.buckets[-1]
    assert (last_bucket.lower, last_bucket.upper) == (0.95, 1.00)
    assert last_bucket.count == 1
    assert sum(bucket.count for bucket in report.buckets) == 1


def test_confidence_report_empty_cache_has_no_observed_floor() -> None:
    report = build_confidence_report([], threshold=0.80)

    assert report.total_count == 0
    assert report.observed_min_confidence is None
    assert report.cache_pre_filtered is False
    assert all(bucket.count == 0 and not bucket.cache_filtered for bucket in report.buckets)


def test_format_confidence_report_reports_filter_note_and_bucket_lines() -> None:
    report = build_confidence_report([record("overture:1", confidence=0.90)], threshold=0.80)

    rendered = format_confidence_report(report, cache_path=Path("data/x.parquet"))

    assert "records in cache: 1" in rendered
    assert "pass current threshold (>= 0.80): 1/1" in rendered
    assert "cache filtered, re-download required" in rendered
    assert "- cafe: 1" in rendered
