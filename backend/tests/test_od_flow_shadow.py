from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from app.scoring.od_flow_shadow import (
    ODFlowObservation,
    ODZoneHourAggregate,
    ODZoneCentroid,
    aggregate_od_flow_shadow,
)


def _flow(
    origin: str,
    destination: str,
    flow: float,
    *,
    departure_hour: int = 9,
    arrival_hour: int = 9,
    purpose: str = "commute",
) -> ODFlowObservation:
    return ODFlowObservation(
        origin,
        destination,
        departure_hour,
        arrival_hour,
        purpose,
        flow,
    )


def _centroid(zone: str, lat: float, lng: float) -> ODZoneCentroid:
    return ODZoneCentroid(zone_id=zone, lat=lat, lng=lng)


def _by_key(
    values: tuple[ODZoneHourAggregate, ...],
) -> dict[tuple[str, int], ODZoneHourAggregate]:
    return {(value.zone_id, value.hour): value for value in values}


def test_totals_net_and_purpose_ratios_include_intrazonal_flow() -> None:
    observations = [
        _flow(
            "west",
            "center",
            30,
            departure_hour=8,
            arrival_hour=9,
            purpose="shopping",
        ),
        _flow(
            "center",
            "center",
            20,
            departure_hour=8,
            arrival_hour=9,
            purpose="other",
        ),
        _flow(
            "center",
            "east",
            10,
            departure_hour=9,
            arrival_hour=10,
            purpose="commute",
        ),
    ]

    results = _by_key(aggregate_od_flow_shadow(observations, []))
    arrival = results[("center", 9)]
    departure = results[("center", 8)]

    assert arrival.inbound == 50
    assert arrival.outbound == 10
    assert arrival.net == 40
    assert arrival.intrazonal_inbound == 20
    assert arrival.intrazonal_outbound == 0
    assert [(item.purpose, item.total, item.ratio) for item in arrival.purposes] == [
        ("other", 20, 0.4),
        ("shopping", 30, 0.6),
    ]
    assert departure.inbound == 0
    assert departure.outbound == 20
    assert departure.net == -20
    assert departure.intrazonal_inbound == 0
    assert departure.intrazonal_outbound == 20
    assert departure.purposes == ()


def test_direction_is_origin_to_destination_and_intrazonal_is_excluded() -> None:
    observations = [
        _flow("west", "center", 30),
        _flow("center", "center", 1_000),
    ]
    centroids = [
        _centroid("west", 37.5, 126.9),
        _centroid("center", 37.5, 127.0),
    ]

    result = _by_key(aggregate_od_flow_shadow(observations, centroids))[("center", 9)]

    assert result.direction_east == pytest.approx(1.0, abs=1e-6)
    assert result.direction_north == pytest.approx(0.0, abs=1e-3)
    assert result.direction_bearing_deg == pytest.approx(90.0, abs=0.1)
    assert result.direction_strength == pytest.approx(1.0)
    assert result.direction_coverage == pytest.approx(1.0)


def test_opposing_flows_cancel_direction_strength() -> None:
    observations = [
        _flow("west", "center", 10),
        _flow("east", "center", 10),
    ]
    centroids = [
        _centroid("west", 0, -1),
        _centroid("center", 0, 0),
        _centroid("east", 0, 1),
    ]

    result = _by_key(aggregate_od_flow_shadow(observations, centroids))[("center", 9)]

    assert result.direction_east == pytest.approx(0.0, abs=1e-15)
    assert result.direction_north == pytest.approx(0.0, abs=1e-15)
    assert result.direction_strength == pytest.approx(0.0, abs=1e-15)
    assert result.direction_bearing_deg is None
    assert result.direction_coverage == 1.0


def test_missing_or_coincident_centroids_reduce_direction_coverage() -> None:
    observations = [
        _flow("known", "center", 20),
        _flow("missing", "center", 30),
        _flow("coincident", "center", 10),
        _flow("center", "center", 1_000),
    ]
    centroids = [
        _centroid("known", 37.4, 127.0),
        _centroid("center", 37.5, 127.0),
        _centroid("coincident", 37.5, 127.0),
    ]

    result = _by_key(aggregate_od_flow_shadow(observations, centroids))[("center", 9)]

    assert result.direction_coverage == pytest.approx(20 / 60)
    assert result.direction_bearing_deg == pytest.approx(0.0, abs=1e-9)
    assert result.direction_strength == pytest.approx(1.0)


def test_missing_destination_centroid_has_zero_direction_coverage() -> None:
    result = _by_key(aggregate_od_flow_shadow(
        [_flow("known", "missing-destination", 20)],
        [_centroid("known", 37.4, 127.0)],
    ))[("missing-destination", 9)]

    assert result.inbound == 20
    assert result.direction_coverage == 0.0
    assert result.direction_east is None
    assert result.direction_north is None
    assert result.direction_bearing_deg is None
    assert result.direction_strength is None


def test_only_intrazonal_flow_has_no_direction_evidence() -> None:
    result = _by_key(aggregate_od_flow_shadow(
        [_flow("center", "center", 5)],
        [_centroid("center", 37.5, 127.0)],
    ))[("center", 9)]

    assert result.intrazonal_inbound == 5
    assert result.intrazonal_outbound == 5
    assert result.direction_east is None
    assert result.direction_north is None
    assert result.direction_bearing_deg is None
    assert result.direction_strength is None
    assert result.direction_coverage == 0.0


def test_output_is_input_order_independent_and_sorted() -> None:
    observations = [
        _flow("b", "z", 1, departure_hour=7, arrival_hour=10, purpose="other"),
        _flow(
            "a", "z", 1e16, departure_hour=7, arrival_hour=10, purpose="shopping"
        ),
        _flow("c", "z", 1, departure_hour=8, arrival_hour=10, purpose="shopping"),
        _flow("z", "a", 3, departure_hour=6, arrival_hour=8),
    ]
    centroids = [
        _centroid("a", 0, 0),
        _centroid("b", 0, 1),
        _centroid("c", 1, 0),
        _centroid("z", 1, 1),
    ]

    forward = aggregate_od_flow_shadow(observations, centroids)
    reverse = aggregate_od_flow_shadow(
        list(reversed(observations)), list(reversed(centroids))
    )

    assert forward == reverse
    assert [(item.zone_id, item.hour) for item in forward] == [
        ("a", 7),
        ("a", 8),
        ("b", 7),
        ("c", 8),
        ("z", 6),
        ("z", 10),
    ]
    assert _by_key(forward)[("z", 10)].inbound == 1e16 + 2
    assert _by_key(forward)[("b", 7)].inbound == 0
    assert _by_key(forward)[("b", 7)].outbound == 1


def test_zero_rows_do_not_create_empty_aggregates() -> None:
    assert aggregate_od_flow_shadow([_flow("a", "b", 0)], []) == ()
    assert aggregate_od_flow_shadow([], []) == ()


@pytest.mark.parametrize(
    ("observation", "message"),
    [
        (_flow("", "b", 1), "origin_id"),
        (_flow("a", " b", 1), "destination_id"),
        (_flow("a", "b", 1, purpose=""), "purpose"),
        (_flow("a", "b", -1), "flow"),
        (_flow("a", "b", float("nan")), "flow"),
        (_flow("a", "b", 1, arrival_hour=24), "arrival_hour"),
        (_flow("a", "b", 1, departure_hour=-1), "departure_hour"),
        (replace(_flow("a", "b", 1), arrival_hour=True), "arrival_hour"),
        (replace(_flow("a", "b", 1), departure_hour=True), "departure_hour"),
    ],
)
def test_invalid_observations_fail_closed(observation, message) -> None:
    with pytest.raises(ValueError, match=message):
        aggregate_od_flow_shadow([observation], [])


def test_invalid_or_duplicate_centroids_fail_closed() -> None:
    with pytest.raises(ValueError, match="duplicate centroid"):
        aggregate_od_flow_shadow(
            [_flow("a", "b", 1)],
            [_centroid("a", 0, 0), _centroid("a", 1, 1)],
        )
    with pytest.raises(ValueError, match="lat"):
        aggregate_od_flow_shadow([], [_centroid("a", 91, 0)])
    with pytest.raises(ValueError, match="lng"):
        aggregate_od_flow_shadow([], [_centroid("a", 0, float("inf"))])


def test_result_dataclasses_are_frozen() -> None:
    result = aggregate_od_flow_shadow([_flow("a", "b", 1)], [])[0]
    with pytest.raises(FrozenInstanceError):
        result.inbound = 2  # type: ignore[misc]
