"""Strict parser for Seoul 250m living-population bulk CSV files.

The OA-22784 bulk download was measured on 2026-07-12: its CSV member is
CP949 encoded and may mask even ``생활인구합계`` with ``*``.  Parsing keeps the
source token and an explicit mask flag; imputation belongs to the offline
experiment layer so this module never turns a masked observation into an
invented measurement.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from app.ingest.national_grid import decode_cell_id


CSV_ENCODING: Final = "cp949"
MASK_TOKEN: Final = "*"
NON_NEGATIVE_DECIMAL_PATTERN: Final = re.compile(r"[0-9]+(?:\.[0-9]*)?")
REQUIRED_COLUMNS: Final = (
    "일자",
    "시간",
    "행정동코드",
    "250M격자",
    "생활인구합계",
)


class LivingPopulationCsvError(ValueError):
    """Raised when a bulk CSV cannot be parsed without guessing."""


@dataclass(frozen=True, slots=True)
class LivingPopulationRecord:
    """One normalized observation with its original total-population token."""

    observed_date: date
    hour: int
    administrative_dong_code: str
    cell_id: str
    total_population: Decimal | None
    total_population_raw: str
    total_population_masked: bool


def _row_error(line_number: int, message: str) -> LivingPopulationCsvError:
    return LivingPopulationCsvError(f"CSV line {line_number}: {message}")


def _parse_date(value: str, *, line_number: int) -> date:
    text = value.strip()
    if len(text) != 8 or not text.isascii() or not text.isdigit():
        raise _row_error(line_number, "일자 must be YYYYMMDD ASCII digits")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        raise _row_error(line_number, f"일자 is not a calendar date: {text!r}") from None


def _parse_hour(value: str, *, line_number: int) -> int:
    text = value.strip()
    if len(text) != 2 or not text.isascii() or not text.isdigit():
        raise _row_error(line_number, "시간 must be two ASCII digits (00-23)")
    hour = int(text)
    if not 0 <= hour <= 23:
        raise _row_error(line_number, f"시간 is outside 00-23: {text!r}")
    return hour


def _parse_dong_code(value: str, *, line_number: int) -> str:
    # The verified source pads H_DNG_CD with trailing spaces.  Strip only the
    # transport padding, then validate the canonical administrative code.
    text = value.strip()
    if len(text) != 8 or not text.isascii() or not text.isdigit():
        raise _row_error(
            line_number, "행정동코드 must normalize to eight ASCII digits"
        )
    return text


def _parse_cell_id(value: str, *, line_number: int) -> str:
    text = value.strip()
    try:
        return decode_cell_id(text).cell_id
    except ValueError as exc:
        raise _row_error(line_number, f"invalid 250M격자: {exc}") from None


def _parse_total(value: str, *, line_number: int) -> tuple[Decimal | None, bool]:
    text = value.strip()
    if text == MASK_TOKEN:
        return None, True
    if not text:
        raise _row_error(line_number, "생활인구합계 must be a number or '*'")
    if NON_NEGATIVE_DECIMAL_PATTERN.fullmatch(text) is None:
        raise _row_error(
            line_number, f"invalid 생활인구합계 value: {text!r}"
        )
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise _row_error(
            line_number, f"invalid 생활인구합계 value: {text!r}"
        ) from None
    if not number.is_finite() or number < 0:
        raise _row_error(
            line_number, "생활인구합계 must be a finite non-negative number"
        )
    return number, False


def _validate_header(fieldnames: Sequence[str] | None) -> None:
    if fieldnames is None:
        raise LivingPopulationCsvError("CSV is empty or has no header")
    duplicates = sorted(
        {name for name in fieldnames if fieldnames.count(name) > 1}
    )
    if duplicates:
        raise LivingPopulationCsvError(
            "duplicate CSV columns: " + ", ".join(duplicates)
        )
    missing = sorted(set(REQUIRED_COLUMNS) - set(fieldnames))
    if missing:
        raise LivingPopulationCsvError(
            "missing CSV columns: " + ", ".join(missing)
        )


def iter_living_population_csv(
    path: str | Path,
) -> Iterator[LivingPopulationRecord]:
    """Yield validated OA-22784 records from one extracted bulk CSV member.

    Extra demographic columns are accepted because the baseline experiment
    only consumes the verified five-column identity/total subset.  Missing,
    duplicate, or ragged columns are rejected.  The iterator streams rows so
    daily and monthly bulk files do not need to fit in memory.
    """

    csv_path = Path(path)
    try:
        with csv_path.open("r", encoding=CSV_ENCODING, newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            _validate_header(reader.fieldnames)
            for row in reader:
                line_number = reader.line_num
                if None in row or any(
                    row[column] is None for column in REQUIRED_COLUMNS
                ):
                    raise _row_error(line_number, "row has the wrong number of columns")
                raw_total = row["생활인구합계"]
                total, masked = _parse_total(raw_total, line_number=line_number)
                yield LivingPopulationRecord(
                    observed_date=_parse_date(row["일자"], line_number=line_number),
                    hour=_parse_hour(row["시간"], line_number=line_number),
                    administrative_dong_code=_parse_dong_code(
                        row["행정동코드"], line_number=line_number
                    ),
                    cell_id=_parse_cell_id(row["250M격자"], line_number=line_number),
                    total_population=total,
                    total_population_raw=raw_total,
                    total_population_masked=masked,
                )
    except UnicodeDecodeError as exc:
        raise LivingPopulationCsvError(
            f"CSV must be {CSV_ENCODING} encoded: {exc}"
        ) from None
    except csv.Error as exc:
        raise LivingPopulationCsvError(f"invalid CSV syntax: {exc}") from None
    except OSError as exc:
        raise LivingPopulationCsvError(f"cannot read CSV: {exc}") from exc
