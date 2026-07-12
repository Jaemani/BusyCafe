from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from scripts.prepare_eval_observations import (
    FieldSession,
    OUTPUT_FIELDS,
    load_reviewed_candidates,
    main,
    parse_observers,
    parse_sessions,
    render_worksheet,
)


def _write_candidates(path: Path, rows: str) -> Path:
    path.write_text(
        "cafe_id,name,road_address,lat,lng,hotspot_name,distance_band,"
        "poi_valid,exclusion_reason\n" + rows,
        encoding="utf-8",
    )
    return path


def _candidate_row(
    cafe_id: int,
    *,
    hotspot: str = "Hongdae",
    poi_valid: str = "true",
    exclusion_reason: str = "",
    band: str = "near",
) -> str:
    return (
        f"{cafe_id},cafe {cafe_id},address {cafe_id},37.1,127.1,"
        f"{hotspot},{band},{poi_valid},{exclusion_reason}\n"
    )


def _session_candidates(
    hotspot: str, start: int, *, extra_band: str = "near"
) -> str:
    bands = ["near", "mid", "fringe", extra_band]
    return "".join(
        _candidate_row(start + offset, hotspot=hotspot, band=band)
        for offset, band in enumerate(bands)
    )


def test_reviewed_candidates_expand_by_slot_and_exclude_invalid_pois(
    tmp_path: Path,
) -> None:
    candidate_path = _write_candidates(
        tmp_path / "candidates.csv",
        "20,valid cafe,address 20,37.1,127.1,Hongdae,near, TRUE ,\n"
        + _candidate_row(21, hotspot="Hongdae", band="near")
        + _candidate_row(22, hotspot="Hongdae", band="mid")
        + _candidate_row(23, hotspot="Hongdae", band="fringe")
        + "10,wrong place,address 10,37.2,127.2,Hongdae,mid,false,not a cafe\n"
        + "30,another cafe,address 30,37.3,127.3,Seongsu,near,true,\n"
        + _candidate_row(31, hotspot="Seongsu", band="mid")
        + _candidate_row(32, hotspot="Seongsu", band="mid")
        + _candidate_row(33, hotspot="Seongsu", band="fringe"),
    )

    candidates = load_reviewed_candidates(candidate_path)
    rendered = render_worksheet(
        candidates,
        [
            FieldSession("Hongdae", "hongdae-am"),
            FieldSession("Seongsu", "seongsu-pm"),
        ],
        ("observer-a", "observer-b"),
    )
    rows = list(csv.DictReader(io.StringIO(rendered)))

    assert tuple(rows[0]) == OUTPUT_FIELDS
    primary_rows = [row for row in rows if row["observation_role"] == "primary"]
    reliability_rows = [
        row for row in rows if row["observation_role"] == "reliability"
    ]
    assert len(primary_rows) == 8
    assert len(reliability_rows) == 8
    assert {row["distance_band"] for row in reliability_rows} == {
        "near",
        "mid",
        "fringe",
    }
    assert {(row["slot"], row["cafe_id"]) for row in primary_rows} == {
        ("hongdae-am", "20"),
        ("hongdae-am", "21"),
        ("hongdae-am", "22"),
        ("hongdae-am", "23"),
        ("seongsu-pm", "30"),
        ("seongsu-pm", "31"),
        ("seongsu-pm", "32"),
        ("seongsu-pm", "33"),
    }
    assert rows[0]["name"] == "valid cafe"
    assert rows[0]["road_address"] == "address 20"
    assert rows[0]["lat"] == "37.1"
    assert rows[0]["lng"] == "127.1"
    assert rows[0]["hotspot_name"] == "Hongdae"
    assert rows[0]["distance_band"] == "near"
    assert rows[0]["observer_id"] == "observer-a"
    assert rows[0]["observation_role"] == "primary"
    for row in rows:
        assert row["observed_at"] == ""
        assert row["observed_area_level"] == ""
        assert row["observed_venue_level"] == ""
        assert row["pedestrians_per_min"] == ""
        assert row["flow_obstruction"] == ""
        assert row["observer_notes"] == ""


@pytest.mark.parametrize("poi_valid", ["", "yes", "unknown"])
def test_unreviewed_or_ambiguous_poi_decisions_fail_closed(
    tmp_path: Path, poi_valid: str
) -> None:
    candidate_path = _write_candidates(
        tmp_path / "candidates.csv", _candidate_row(1, poi_valid=poi_valid)
    )

    with pytest.raises(ValueError, match="row 2.*poi_valid"):
        load_reviewed_candidates(candidate_path)


def test_false_poi_requires_exclusion_reason_and_ids_are_unique(
    tmp_path: Path,
) -> None:
    missing_reason = _write_candidates(
        tmp_path / "missing.csv",
        _candidate_row(1, poi_valid="false", exclusion_reason="   "),
    )
    with pytest.raises(ValueError, match="row 2.*exclusion_reason"):
        load_reviewed_candidates(missing_reason)

    duplicate = _write_candidates(
        tmp_path / "duplicate.csv",
        _candidate_row(1) + _candidate_row(1),
    )
    with pytest.raises(ValueError, match="row 3.*duplicate cafe_id"):
        load_reviewed_candidates(duplicate)


def test_all_invalid_and_session_without_valid_candidates_fail_closed(
    tmp_path: Path,
) -> None:
    all_invalid = _write_candidates(
        tmp_path / "all-invalid.csv",
        _candidate_row(1, poi_valid="false", exclusion_reason="not a cafe"),
    )
    with pytest.raises(ValueError, match="no valid reviewed POIs"):
        load_reviewed_candidates(all_invalid)

    mixed = _write_candidates(
        tmp_path / "mixed.csv",
        _candidate_row(1)
        + _candidate_row(
            2,
            hotspot="Seongsu",
            poi_valid="false",
            exclusion_reason="closed",
        ),
    )
    candidates = load_reviewed_candidates(mixed)
    with pytest.raises(ValueError, match="Seongsu"):
        render_worksheet(
            candidates,
            [FieldSession("Seongsu", "seongsu-am")],
            ("a", "b"),
        )
    with pytest.raises(ValueError, match="Unknown"):
        render_worksheet(
            candidates,
            [FieldSession("Unknown", "unknown-am")],
            ("a", "b"),
        )


def test_cli_requires_session_uses_stdout_and_refuses_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate_path = _write_candidates(
        tmp_path / "candidates.csv", _session_candidates("Hongdae", 1)
    )

    with pytest.raises(SystemExit) as missing_session:
        main([str(candidate_path)])
    assert missing_session.value.code == 2
    capsys.readouterr()

    cli_base = [
        str(candidate_path),
        "--session",
        "Hongdae=session-1",
        "--observer",
        "a",
        "--observer",
        "b",
    ]
    assert main(cli_base) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert list(csv.DictReader(io.StringIO(captured.out)))[0]["cafe_id"] == "1"

    output = tmp_path / "observations.csv"
    assert (
        main(
            [
                *cli_base,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    original = output.read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as overwrite:
        main(
            [
                *cli_base,
                "--output",
                str(output),
            ]
        )
    assert overwrite.value.code == 2
    assert output.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("sessions", [[], [""], ["Hongdae="], ["=slot"]])
def test_missing_or_blank_session_parts_fail_closed(sessions: list[str]) -> None:
    with pytest.raises(ValueError, match="session"):
        parse_sessions(sessions)


def test_slot_ids_are_globally_unique() -> None:
    with pytest.raises(ValueError, match="slot IDs"):
        parse_sessions(["Hongdae=same", "Seongsu=same"])

    assert parse_sessions([" Hongdae = morning "]) == (
        FieldSession("Hongdae", "morning"),
    )


@pytest.mark.parametrize(
    "observers", [[], ["a"], ["a", "b", "c"], ["a", "a"], ["a", " "]]
)
def test_exactly_two_unique_nonblank_observers_are_required(
    observers: list[str],
) -> None:
    with pytest.raises(ValueError, match="observer"):
        parse_observers(observers)

    assert parse_observers([" observer-a ", "observer-b"]) == (
        "observer-a",
        "observer-b",
    )


def test_reliability_overlap_requires_all_bands_and_rotated_band_capacity(
    tmp_path: Path,
) -> None:
    missing_band = _write_candidates(
        tmp_path / "missing-band.csv",
        _candidate_row(1, band="near")
        + _candidate_row(2, band="near")
        + _candidate_row(3, band="mid")
        + _candidate_row(4, band="mid"),
    )
    with pytest.raises(ValueError, match="every distance band"):
        render_worksheet(
            load_reviewed_candidates(missing_band),
            [FieldSession("Hongdae", "slot-a")],
            ("a", "b"),
        )


def test_fourth_overlap_rotates_deterministically_across_distance_bands(
    tmp_path: Path,
) -> None:
    candidate_rows = "".join(
        _candidate_row(cafe_id, band=band)
        for band, ids in (
            ("near", range(1, 5)),
            ("mid", range(5, 9)),
            ("fringe", range(9, 13)),
        )
        for cafe_id in ids
    )
    candidate_path = _write_candidates(tmp_path / "candidates.csv", candidate_rows)
    rendered = render_worksheet(
        load_reviewed_candidates(candidate_path),
        [
            FieldSession("Hongdae", "slot-0"),
            FieldSession("Hongdae", "slot-1"),
            FieldSession("Hongdae", "slot-2"),
        ],
        ("a", "b"),
    )
    rows = list(csv.DictReader(io.StringIO(rendered)))
    reliability_by_slot = {
        slot: {
            int(row["cafe_id"])
            for row in rows
            if row["slot"] == slot and row["observation_role"] == "reliability"
        }
        for slot in ("slot-0", "slot-1", "slot-2")
    }

    assert reliability_by_slot == {
        "slot-0": {1, 2, 5, 9},
        "slot-1": {2, 6, 7, 10},
        "slot-2": {3, 7, 11, 12},
    }
