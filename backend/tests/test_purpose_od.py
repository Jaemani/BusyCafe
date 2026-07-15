from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.ingest.purpose_od import (
    PurposeOdCsvError,
    PurposeOdRecord,
    iter_purpose_od_csv,
    iter_purpose_od_zip,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"
MEASURED_FIXTURE = FIXTURES / "purpose_od_sample_20260630.csv"
HEADER = (
    "o_admdong_cd,d_admdong_cd,st_time_cd,fns_time_cd,in_forn_div_nm,"
    "forn_citiz_nm,move_purpose,move_dist,move_time,cnt,etl_ymd"
)
VALID_ROW = "11410710,41285000,0700,0820,내국인,한국,1,17885,86,3.27,20260630"


def _write_csv(tmp_path: Path, rows: list[str], *, header: str = HEADER) -> Path:
    path = tmp_path / "input.csv"
    path.write_text("\n".join((header, *rows)) + "\n", encoding="utf-8")
    return path


def _write_zip(tmp_path: Path, members: dict[str, bytes]) -> Path:
    path = tmp_path / "input.zip"
    with ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def test_parses_measured_utf8_sample() -> None:
    records = tuple(iter_purpose_od_csv(MEASURED_FIXTURE))

    assert len(records) == 5
    first = records[0]
    assert first == PurposeOdRecord(
        origin_administrative_dong_code="11410710",
        destination_administrative_dong_code="41285000",
        start_minute=7 * 60,
        finish_minute=8 * 60 + 20,
        resident_type="내국인",
        nationality="한국",
        purpose=1,
        distance_m=17_885,
        duration_min=86,
        estimated_count=Decimal("3.27"),
        observed_date=date(2026, 6, 30),
    )
    assert records[1].start_minute == 5 * 60
    assert records[1].finish_minute == 6 * 60
    assert records[1].departure_hour == 5
    assert records[1].arrival_hour == 6
    assert records[2].duration_min == 0
    assert records[3].finish_minute == 11 * 60
    assert records[3].arrival_hour == 11
    assert records[4].distance_m == 0
    assert records[4].purpose == 7


def test_record_is_frozen() -> None:
    record = next(iter_purpose_od_csv(MEASURED_FIXTURE))
    with pytest.raises(FrozenInstanceError):
        record.purpose = 2  # type: ignore[misc]


def test_zip_parser_streams_the_same_measured_records(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path,
        {"seoul_purpose_admdong3_final_20260630.csv": MEASURED_FIXTURE.read_bytes()},
    )

    assert tuple(iter_purpose_od_zip(archive)) == tuple(
        iter_purpose_od_csv(MEASURED_FIXTURE)
    )


@pytest.mark.parametrize(
    ("header", "message"),
    [
        (HEADER.rsplit(",", 1)[0], "header must exactly equal"),
        (HEADER + ",unexpected", "header must exactly equal"),
        (
            HEADER.replace(
                "o_admdong_cd,d_admdong_cd", "d_admdong_cd,o_admdong_cd"
            ),
            "header must exactly equal",
        ),
        (HEADER.replace("d_admdong_cd", "o_admdong_cd", 1), "header must exactly equal"),
    ],
)
def test_requires_exact_eleven_column_header(
    tmp_path: Path, header: str, message: str
) -> None:
    with pytest.raises(PurposeOdCsvError, match=message):
        tuple(iter_purpose_od_csv(_write_csv(tmp_path, [], header=header)))


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (VALID_ROW.replace("20260630", "2026063"), "YYYYMMDD"),
        (VALID_ROW.replace("20260630", "20260230"), "calendar date"),
        (VALID_ROW.replace("0700", "7", 1), "not a verified"),
        (VALID_ROW.replace("0700", "2400", 1), "not a verified"),
        (VALID_ROW.replace("0700", "0710", 1), "not a verified"),
        (VALID_ROW.replace("0700", "1020", 1), "not a verified"),
        (VALID_ROW.replace("0820", "0860", 1), "not a verified"),
        (VALID_ROW.replace("11410710", "1141071", 1), "eight ASCII digits"),
        (VALID_ROW.replace("41285000", "41285A00", 1), "eight ASCII digits"),
        (VALID_ROW.replace(",1,17885", ",0,17885"), "integer from 1 to 7"),
        (VALID_ROW.replace(",1,17885", ",8,17885"), "integer from 1 to 7"),
        (VALID_ROW.replace("17885,86", "-1,86"), "non-negative integer"),
        (VALID_ROW.replace("17885,86", "1.5,86"), "non-negative integer"),
        (VALID_ROW.replace("86,3.27", "-1,3.27"), "non-negative integer"),
        (VALID_ROW.replace("3.27", "0"), "positive decimal"),
        (VALID_ROW.replace("3.27", "-1"), "positive decimal"),
        (VALID_ROW.replace("3.27", "NaN"), "positive decimal"),
        (VALID_ROW.replace("3.27", "1e2"), "positive decimal"),
        (VALID_ROW.replace("내국인", ""), "non-empty text"),
        (VALID_ROW.replace("한국", " 한국"), "surrounding whitespace"),
    ],
)
def test_rejects_invalid_record_fields(
    tmp_path: Path, row: str, message: str
) -> None:
    with pytest.raises(PurposeOdCsvError, match=message):
        tuple(iter_purpose_od_csv(_write_csv(tmp_path, [row])))


@pytest.mark.parametrize(
    "row",
    [
        VALID_ROW.rsplit(",", 1)[0],
        VALID_ROW + ",unexpected",
    ],
)
def test_rejects_ragged_rows(tmp_path: Path, row: str) -> None:
    with pytest.raises(PurposeOdCsvError, match="wrong number of columns"):
        tuple(iter_purpose_od_csv(_write_csv(tmp_path, [row])))


def test_rejects_non_utf8_csv(tmp_path: Path) -> None:
    path = tmp_path / "cp949.csv"
    path.write_bytes((HEADER + "\n" + VALID_ROW + "\n").encode("cp949"))
    with pytest.raises(PurposeOdCsvError, match="utf-8"):
        tuple(iter_purpose_od_csv(path))


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ({}, "exactly one"),
        ({"one.csv": b"", "two.csv": b""}, "exactly one"),
        ({"one.txt": b""}, "must have a .csv"),
    ],
)
def test_rejects_invalid_zip_member_layout(
    tmp_path: Path, members: dict[str, bytes], message: str
) -> None:
    with pytest.raises(PurposeOdCsvError, match=message):
        tuple(iter_purpose_od_zip(_write_zip(tmp_path, members)))


def test_rejects_bad_zip(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a ZIP")
    with pytest.raises(PurposeOdCsvError, match="invalid ZIP archive"):
        tuple(iter_purpose_od_zip(path))


def test_rejects_non_utf8_zip_member(tmp_path: Path) -> None:
    payload = (HEADER + "\n" + VALID_ROW + "\n").encode("cp949")
    archive = _write_zip(tmp_path, {"sample.csv": payload})
    with pytest.raises(PurposeOdCsvError, match="utf-8"):
        tuple(iter_purpose_od_zip(archive))
