"""Strict streaming parser for Seoul purpose-based movement OD files.

The OA-22300 bulk file was measured on 2026-07-15.  Its single CSV member is
UTF-8 encoded and has the exact eleven-column header declared below.  Time
codes use either an hourly ``HH`` bucket or a 20-minute ``HHMM`` bucket.
This module normalizes those codes to minutes after midnight without guessing
routes, coordinates, or missing values.
"""

from __future__ import annotations

import csv
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
from pathlib import Path
from typing import Final, TextIO


CSV_ENCODING: Final = "utf-8"
REQUIRED_COLUMNS: Final = (
    "o_admdong_cd",
    "d_admdong_cd",
    "st_time_cd",
    "fns_time_cd",
    "in_forn_div_nm",
    "forn_citiz_nm",
    "move_purpose",
    "move_dist",
    "move_time",
    "cnt",
    "etl_ymd",
)
ADMINISTRATIVE_DONG_PATTERN: Final = re.compile(r"[0-9]{8}")
NON_NEGATIVE_INTEGER_PATTERN: Final = re.compile(r"[0-9]+")
POSITIVE_DECIMAL_PATTERN: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?")
VERIFIED_HOURLY_HOURS: Final = (*range(0, 7), *range(10, 17), *range(20, 24))
VERIFIED_TWENTY_MINUTE_HOURS: Final = (*range(7, 10), *range(17, 20))
VERIFIED_TIME_CODES: Final = frozenset(
    {f"{hour:02d}" for hour in VERIFIED_HOURLY_HOURS}
    | {
        f"{hour:02d}{minute:02d}"
        for hour in VERIFIED_TWENTY_MINUTE_HOURS
        for minute in (0, 20, 40)
    }
)


class PurposeOdCsvError(ValueError):
    """Raised when a purpose OD CSV cannot be parsed without guessing."""


@dataclass(frozen=True, slots=True)
class PurposeOdRecord:
    """One normalized purpose-based origin/destination movement estimate."""

    origin_administrative_dong_code: str
    destination_administrative_dong_code: str
    start_minute: int
    finish_minute: int
    resident_type: str
    nationality: str
    purpose: int
    distance_m: int
    duration_min: int
    estimated_count: Decimal
    observed_date: date

    @property
    def departure_hour(self) -> int:
        """Return the normalized origin hour while retaining 20-minute detail."""

        return self.start_minute // 60

    @property
    def arrival_hour(self) -> int:
        """Return the normalized destination hour while retaining 20-minute detail."""

        return self.finish_minute // 60


def _row_error(line_number: int, message: str) -> PurposeOdCsvError:
    return PurposeOdCsvError(f"CSV line {line_number}: {message}")


def _validate_header(fieldnames: list[str] | None) -> None:
    if fieldnames is None:
        raise PurposeOdCsvError("CSV is empty or has no header")
    if tuple(fieldnames) != REQUIRED_COLUMNS:
        expected = ",".join(REQUIRED_COLUMNS)
        actual = ",".join(fieldnames)
        raise PurposeOdCsvError(
            f"CSV header must exactly equal {expected!r}; got {actual!r}"
        )


def _parse_date(value: str, *, line_number: int) -> date:
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        raise _row_error(line_number, "etl_ymd must be YYYYMMDD ASCII digits")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise _row_error(
            line_number, f"etl_ymd is not a calendar date: {value!r}"
        ) from None


def _parse_time_code(value: str, *, line_number: int, column: str) -> int:
    if value not in VERIFIED_TIME_CODES:
        raise _row_error(
            line_number,
            f"{column} is not a verified OA-22300 HH/HHMM time code: {value!r}",
        )
    hour = int(value[:2])
    minute = 0 if len(value) == 2 else int(value[2:])
    return hour * 60 + minute


def _parse_dong_code(value: str, *, line_number: int, column: str) -> str:
    if ADMINISTRATIVE_DONG_PATTERN.fullmatch(value) is None:
        raise _row_error(line_number, f"{column} must be eight ASCII digits")
    return value


def _parse_text(value: str, *, line_number: int, column: str) -> str:
    if not value or value != value.strip():
        raise _row_error(
            line_number,
            f"{column} must be non-empty text without surrounding whitespace",
        )
    return value


def _parse_purpose(value: str, *, line_number: int) -> int:
    if len(value) != 1 or value not in "1234567":
        raise _row_error(line_number, "move_purpose must be an integer from 1 to 7")
    return int(value)


def _parse_non_negative_integer(
    value: str,
    *,
    line_number: int,
    column: str,
) -> int:
    if NON_NEGATIVE_INTEGER_PATTERN.fullmatch(value) is None:
        raise _row_error(
            line_number, f"{column} must be a non-negative integer"
        )
    return int(value)


def _parse_count(value: str, *, line_number: int) -> Decimal:
    if POSITIVE_DECIMAL_PATTERN.fullmatch(value) is None:
        raise _row_error(line_number, "cnt must be a finite positive decimal")
    try:
        count = Decimal(value)
    except InvalidOperation:
        raise _row_error(
            line_number, "cnt must be a finite positive decimal"
        ) from None
    if not count.is_finite() or count <= 0:
        raise _row_error(line_number, "cnt must be a finite positive decimal")
    return count


def _iter_purpose_od_handle(handle: TextIO) -> Iterator[PurposeOdRecord]:
    reader = csv.DictReader(handle, strict=True)
    _validate_header(reader.fieldnames)
    for row in reader:
        line_number = reader.line_num
        if None in row or any(row[column] is None for column in REQUIRED_COLUMNS):
            raise _row_error(line_number, "row has the wrong number of columns")
        yield PurposeOdRecord(
            origin_administrative_dong_code=_parse_dong_code(
                row["o_admdong_cd"],
                line_number=line_number,
                column="o_admdong_cd",
            ),
            destination_administrative_dong_code=_parse_dong_code(
                row["d_admdong_cd"],
                line_number=line_number,
                column="d_admdong_cd",
            ),
            start_minute=_parse_time_code(
                row["st_time_cd"],
                line_number=line_number,
                column="st_time_cd",
            ),
            finish_minute=_parse_time_code(
                row["fns_time_cd"],
                line_number=line_number,
                column="fns_time_cd",
            ),
            resident_type=_parse_text(
                row["in_forn_div_nm"],
                line_number=line_number,
                column="in_forn_div_nm",
            ),
            nationality=_parse_text(
                row["forn_citiz_nm"],
                line_number=line_number,
                column="forn_citiz_nm",
            ),
            purpose=_parse_purpose(row["move_purpose"], line_number=line_number),
            distance_m=_parse_non_negative_integer(
                row["move_dist"],
                line_number=line_number,
                column="move_dist",
            ),
            duration_min=_parse_non_negative_integer(
                row["move_time"],
                line_number=line_number,
                column="move_time",
            ),
            estimated_count=_parse_count(row["cnt"], line_number=line_number),
            observed_date=_parse_date(row["etl_ymd"], line_number=line_number),
        )


def iter_purpose_od_csv(path: str | Path) -> Iterator[PurposeOdRecord]:
    """Yield validated records from one extracted OA-22300 UTF-8 CSV."""

    csv_path = Path(path)
    try:
        with csv_path.open("r", encoding=CSV_ENCODING, newline="") as handle:
            yield from _iter_purpose_od_handle(handle)
    except UnicodeDecodeError as exc:
        raise PurposeOdCsvError(
            f"CSV must be {CSV_ENCODING} encoded: {exc}"
        ) from None
    except csv.Error as exc:
        raise PurposeOdCsvError(f"invalid CSV syntax: {exc}") from None
    except OSError as exc:
        raise PurposeOdCsvError(f"cannot read CSV: {exc}") from exc


def iter_purpose_od_zip(path: str | Path) -> Iterator[PurposeOdRecord]:
    """Yield records from the sole CSV member of an OA-22300 ZIP, streaming."""

    zip_path = Path(path)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            if len(members) != 1:
                raise PurposeOdCsvError(
                    "ZIP must contain exactly one non-directory CSV member"
                )
            member = members[0]
            if not member.filename.lower().endswith(".csv"):
                raise PurposeOdCsvError("ZIP member must have a .csv filename")
            with archive.open(member, "r") as binary_handle:
                with TextIOWrapper(
                    binary_handle,
                    encoding=CSV_ENCODING,
                    newline="",
                ) as text_handle:
                    yield from _iter_purpose_od_handle(text_handle)
    except UnicodeDecodeError as exc:
        raise PurposeOdCsvError(
            f"ZIP CSV member must be {CSV_ENCODING} encoded: {exc}"
        ) from None
    except csv.Error as exc:
        raise PurposeOdCsvError(f"invalid CSV syntax: {exc}") from None
    except zipfile.BadZipFile as exc:
        raise PurposeOdCsvError(f"invalid ZIP archive: {exc}") from None
    except OSError as exc:
        raise PurposeOdCsvError(f"cannot read ZIP: {exc}") from exc
