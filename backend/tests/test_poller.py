from __future__ import annotations

import copy
import json
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.clients.seoul_citydata import SeoulAPIError
from app.ingest.poller import (
    ParseFailureRecord,
    PollTarget,
    SnapshotRecord,
    build_snapshot_record,
    observed_at_to_utc,
    poll_once,
)


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_citydata_fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURES_DIR / "citydata_sample.json").read_text(encoding="utf-8")
    )


class FakePopulationClient:
    def __init__(self, responses: dict[str, Iterable[Any]]) -> None:
        self._responses = {
            area: iter(area_responses)
            for area, area_responses in responses.items()
        }
        self.calls: list[str] = []

    def fetch_population_raw(self, area_name: str) -> dict[str, Any]:
        self.calls.append(area_name)
        response = next(self._responses[area_name])
        if isinstance(response, Exception):
            raise response
        return response


def fixture_for_area(
    name: str, code: str, *, label: str = "보통"
) -> dict[str, Any]:
    payload = copy.deepcopy(load_citydata_fixture())
    record = payload["SeoulRtd.citydata_ppltn"][0]
    record["AREA_NM"] = name
    record["AREA_CD"] = code
    record["AREA_CONGEST_LVL"] = label
    return payload


def test_observed_at_is_interpreted_as_kst_and_normalized_to_utc() -> None:
    result = observed_at_to_utc("2026-07-11 16:25")

    assert result == datetime(2026, 7, 11, 7, 25, tzinfo=UTC)
    assert result.utcoffset().total_seconds() == 0


def test_build_record_preserves_population_forecast_and_raw_payload() -> None:
    payload = load_citydata_fixture()
    fetched_at = datetime(2026, 7, 11, 16, 26, tzinfo=UTC)

    record = build_snapshot_record(
        PollTarget(hotspot_id=88, area_cd="POI088", area_name="광화문광장"),
        payload,
        fetched_at=fetched_at,
    )

    assert record.hotspot_id == 88
    assert record.congest_level == 2
    assert record.congest_label == "보통"
    assert record.ppltn_min == 6500
    assert record.ppltn_max == 7000
    assert record.fetched_at is fetched_at
    assert record.forecast_json == payload[
        "SeoulRtd.citydata_ppltn"
    ][0]["FCST_PPLTN"]
    assert record.raw_json == payload


def test_poll_once_retries_with_exponential_backoff_then_saves() -> None:
    payload = load_citydata_fixture()
    client = FakePopulationClient(
        {
            "광화문광장": [
                SeoulAPIError("temporary"),
                SeoulAPIError("bad"),
                payload,
            ]
        }
    )
    sleeps: list[float] = []
    saved: list[SnapshotRecord] = []

    report = poll_once(
        [PollTarget(88, "POI088", "광화문광장")],
        client=client,
        save_snapshot=saved.append,
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        clock=lambda: datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        sleep=sleeps.append,
        max_retries=3,
        retry_base_delay_seconds=0.5,
    )

    assert client.calls == ["광화문광장"] * 3
    assert sleeps == [0.5, 1.0]
    assert report.saved == 1
    assert report.failed == 0
    assert len(saved) == 1


def test_poll_report_measures_fetch_and_persistence_without_payload_data() -> None:
    payload = load_citydata_fixture()
    ticks = iter([1.0, 1.25, 2.0, 2.75])

    report = poll_once(
        [PollTarget(88, "POI088", "광화문광장")],
        client=FakePopulationClient({"광화문광장": [payload]}),
        save_snapshot=lambda _: None,
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        clock=lambda: datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        monotonic=lambda: next(ticks),
    )

    assert report.fetch_seconds == pytest.approx(0.25)
    assert report.persistence_seconds == pytest.approx(0.75)


def test_final_failure_does_not_prevent_next_target() -> None:
    good_payload = fixture_for_area("홍대 관광특구", "POI001", label="붐빔")
    client = FakePopulationClient(
        {
            "실패 지역": [SeoulAPIError("down")] * 4,
            "홍대 관광특구": [good_payload],
        }
    )
    sleeps: list[float] = []
    saved: list[SnapshotRecord] = []

    report = poll_once(
        [
            PollTarget(1, "POI999", "실패 지역"),
            PollTarget(2, "POI001", "홍대 관광특구"),
        ],
        client=client,
        save_snapshot=saved.append,
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        clock=lambda: datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        sleep=sleeps.append,
        max_retries=3,
        retry_base_delay_seconds=0.25,
        fetch_concurrency=1,
    )

    assert client.calls == ["실패 지역"] * 4 + ["홍대 관광특구"]
    assert sleeps == [0.25, 0.5, 1.0]
    assert report.targets == 2
    assert report.saved == 1
    assert report.failed == 1
    assert saved[0].hotspot_id == 2
    assert saved[0].congest_level == 4


def test_consecutive_failure_circuit_skips_remaining_targets() -> None:
    targets = [
        PollTarget(index, f"POI{index:03d}", f"실패 지역 {index}")
        for index in range(1, 8)
    ]
    client = FakePopulationClient(
        {
            target.area_name: [SeoulAPIError("down")]
            for target in targets
        }
    )

    report = poll_once(
        targets,
        client=client,
        save_snapshot=lambda _: pytest.fail("unexpected snapshot"),
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        sleep=lambda _: pytest.fail("sleep must not be called"),
        max_retries=0,
        max_consecutive_failures=3,
        fetch_concurrency=1,
    )

    assert client.calls == [target.area_name for target in targets[:3]]
    assert report.targets == 7
    assert report.saved == 0
    assert report.failed == 7


def test_success_resets_consecutive_failure_circuit() -> None:
    targets = [
        PollTarget(1, "POI101", "첫 실패"),
        PollTarget(2, "POI102", "성공 지역"),
        PollTarget(3, "POI103", "둘째 실패"),
        PollTarget(4, "POI104", "셋째 실패"),
    ]
    client = FakePopulationClient(
        {
            "첫 실패": [SeoulAPIError("down")],
            "성공 지역": [fixture_for_area("성공 지역", "POI102")],
            "둘째 실패": [SeoulAPIError("down")],
            "셋째 실패": [SeoulAPIError("down")],
        }
    )
    saved: list[SnapshotRecord] = []

    report = poll_once(
        targets,
        client=client,
        save_snapshot=saved.append,
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        sleep=lambda _: pytest.fail("sleep must not be called"),
        max_retries=0,
        max_consecutive_failures=2,
        fetch_concurrency=1,
    )

    assert client.calls == [target.area_name for target in targets]
    assert report.targets == 4
    assert report.saved == 1
    assert report.failed == 3


def test_poll_rejects_nonpositive_consecutive_failure_limit() -> None:
    with pytest.raises(ValueError, match="max_consecutive_failures must be > 0"):
        poll_once(
            [],
            client=FakePopulationClient({}),
            save_snapshot=lambda _: None,
            save_parse_failure=lambda _: None,
            max_consecutive_failures=0,
        )


def test_persistence_failure_is_isolated_without_refetching() -> None:
    first = fixture_for_area("첫 지역", "POI101")
    second = fixture_for_area("둘째 지역", "POI102")
    client = FakePopulationClient({"첫 지역": [first], "둘째 지역": [second]})
    saved: list[SnapshotRecord] = []

    def persist(record: SnapshotRecord) -> None:
        if record.hotspot_id == 1:
            raise RuntimeError("database unavailable")
        saved.append(record)

    report = poll_once(
        [
            PollTarget(1, "POI101", "첫 지역"),
            PollTarget(2, "POI102", "둘째 지역"),
        ],
        client=client,
        save_snapshot=persist,
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        clock=lambda: datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        sleep=lambda _: pytest.fail("sleep must not be called"),
        fetch_concurrency=1,
    )

    assert client.calls == ["첫 지역", "둘째 지역"]
    assert report.saved == 1
    assert report.failed == 1
    assert [record.hotspot_id for record in saved] == [2]


@pytest.mark.parametrize(
    ("response_name", "response_code"),
    [("다른 장소", "POI088"), ("광화문광장", "POI999")],
)
def test_target_mismatch_is_not_retried_and_preserves_raw_failure(
    response_name: str, response_code: str
) -> None:
    payload = fixture_for_area(response_name, response_code)
    good_payload = fixture_for_area("홍대 관광특구", "POI001")
    client = FakePopulationClient(
        {"광화문광장": [payload], "홍대 관광특구": [good_payload]}
    )
    saved: list[SnapshotRecord] = []
    failures: list[ParseFailureRecord] = []
    sleeps: list[float] = []

    report = poll_once(
        [
            PollTarget(88, "POI088", "광화문광장"),
            PollTarget(1, "POI001", "홍대 관광특구"),
        ],
        client=client,
        save_snapshot=saved.append,
        save_parse_failure=failures.append,
        clock=lambda: datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        sleep=sleeps.append,
        fetch_concurrency=1,
    )

    assert client.calls == ["광화문광장", "홍대 관광특구"]
    assert sleeps == []
    assert report.failed == 1
    assert [record.hotspot_id for record in saved] == [1]
    assert len(failures) == 1
    assert failures[0].hotspot_id == 88
    assert failures[0].error_type == "PopulationTargetMismatch"
    assert failures[0].error_message == "population response target mismatch"
    assert failures[0].raw_json == payload


def test_schema_parse_failure_is_not_retried() -> None:
    payload = load_citydata_fixture()
    del payload["SeoulRtd.citydata_ppltn"][0]["PPLTN_TIME"]
    client = FakePopulationClient({"광화문광장": [payload]})
    failures: list[ParseFailureRecord] = []

    report = poll_once(
        [PollTarget(88, "POI088", "광화문광장")],
        client=client,
        save_snapshot=lambda _: pytest.fail("unexpected snapshot"),
        save_parse_failure=failures.append,
        clock=lambda: datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        sleep=lambda _: pytest.fail("parse failure must not retry"),
    )

    assert client.calls == ["광화문광장"]
    assert report.failed == 1
    assert failures[0].error_type == "ValidationError"
    assert failures[0].error_message == "population response validation failed"
    assert failures[0].raw_json == payload


def test_fetches_concurrently_but_persists_in_target_order() -> None:
    targets = [
        PollTarget(index, f"POI{index:03d}", f"지역 {index}")
        for index in range(1, 4)
    ]
    payloads = {
        target.area_name: fixture_for_area(target.area_name, target.area_cd)
        for target in targets
    }
    barrier = threading.Barrier(len(targets))
    lock = threading.Lock()
    active = 0
    max_active = 0

    class ConcurrentClient:
        def fetch_population_raw(self, area_name: str) -> dict[str, Any]:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                barrier.wait(timeout=2)
                return payloads[area_name]
            finally:
                with lock:
                    active -= 1

    saved: list[SnapshotRecord] = []
    report = poll_once(
        targets,
        client=ConcurrentClient(),
        save_snapshot=saved.append,
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        clock=lambda: datetime(2026, 7, 11, 7, 26, tzinfo=UTC),
        fetch_concurrency=3,
    )

    assert max_active == 3
    assert [record.hotspot_id for record in saved] == [1, 2, 3]
    assert report.saved == 3
    assert report.failed == 0


def test_poll_rejects_nonpositive_fetch_concurrency() -> None:
    with pytest.raises(ValueError, match="fetch_concurrency must be > 0"):
        poll_once(
            [],
            client=FakePopulationClient({}),
            save_snapshot=lambda _: None,
            save_parse_failure=lambda _: None,
            fetch_concurrency=0,
        )


def test_concurrent_fetch_never_calls_beyond_circuit_budget() -> None:
    targets = [
        PollTarget(index, f"POI{index:03d}", f"실패 지역 {index}")
        for index in range(1, 11)
    ]
    calls = 0
    lock = threading.Lock()

    class FailingClient:
        def fetch_population_raw(self, _area_name: str) -> dict[str, Any]:
            nonlocal calls
            with lock:
                calls += 1
            raise SeoulAPIError("down")

    report = poll_once(
        targets,
        client=FailingClient(),
        save_snapshot=lambda _: pytest.fail("unexpected snapshot"),
        save_parse_failure=lambda _: pytest.fail("unexpected parse failure"),
        sleep=lambda _: pytest.fail("sleep must not be called"),
        max_retries=0,
        max_consecutive_failures=5,
        fetch_concurrency=4,
    )

    assert calls == 5
    assert report.saved == 0
    assert report.failed == len(targets)
