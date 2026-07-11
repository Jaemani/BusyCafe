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
) -> str:
    return (
        f"{cafe_id},cafe {cafe_id},address {cafe_id},37.1,127.1,"
        f"{hotspot},near,{poi_valid},{exclusion_reason}\n"
    )


def test_reviewed_candidates_expand_by_slot_and_exclude_invalid_pois(
    tmp_path: Path,
) -> None:
    candidate_path = _write_candidates(
        tmp_path / "candidates.csv",
        "20,valid cafe,address 20,37.1,127.1,Hongdae,near, TRUE ,\n"
        "10,wrong place,address 10,37.2,127.2,Hongdae,mid,false,not a cafe\n"
        "30,another cafe,address 30,37.3,127.3,Seongsu,fringe,true,\n",
    )

    candidates = load_reviewed_candidates(candidate_path)
    rendered = render_worksheet(
        candidates,
        [
            FieldSession("Hongdae", "hongdae-am"),
            FieldSession("Seongsu", "seongsu-pm"),
        ],
    )
    rows = list(csv.DictReader(io.StringIO(rendered)))

    assert tuple(rows[0]) == OUTPUT_FIELDS
    assert [(row["slot"], row["cafe_id"]) for row in rows] == [
        ("hongdae-am", "20"),
        ("seongsu-pm", "30"),
    ]
    assert rows[0]["name"] == "valid cafe"
    assert rows[0]["road_address"] == "address 20"
    assert rows[0]["lat"] == "37.1"
    assert rows[0]["lng"] == "127.1"
    assert rows[0]["hotspot_name"] == "Hongdae"
    assert rows[0]["distance_band"] == "near"
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
        render_worksheet(candidates, [FieldSession("Seongsu", "seongsu-am")])
    with pytest.raises(ValueError, match="Unknown"):
        render_worksheet(candidates, [FieldSession("Unknown", "unknown-am")])


def test_cli_requires_session_uses_stdout_and_refuses_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate_path = _write_candidates(
        tmp_path / "candidates.csv", _candidate_row(1)
    )

    with pytest.raises(SystemExit) as missing_session:
        main([str(candidate_path)])
    assert missing_session.value.code == 2
    capsys.readouterr()

    assert main([str(candidate_path), "--session", "Hongdae=session-1"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert list(csv.DictReader(io.StringIO(captured.out)))[0]["cafe_id"] == "1"

    output = tmp_path / "observations.csv"
    assert (
        main(
            [
                str(candidate_path),
                "--session",
                "Hongdae=session-1",
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
                str(candidate_path),
                "--session",
                "Hongdae=session-1",
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
