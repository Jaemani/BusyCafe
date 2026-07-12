from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingest.living_population import (
    LivingPopulationCsvError,
    iter_living_population_csv,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"
DERIVED_FIXTURE = FIXTURES / "living_population_minimal_cp949.csv"
HEADER = "일자,시간,행정동코드,250M격자,생활인구합계"


def _write_csv(tmp_path: Path, rows: list[str], *, header: str = HEADER) -> Path:
    path = tmp_path / "input.csv"
    path.write_text("\n".join((header, *rows)) + "\n", encoding="cp949")
    return path


def test_parses_verified_cp949_subset_and_preserves_mask_state() -> None:
    records = tuple(iter_living_population_csv(DERIVED_FIXTURE))

    assert len(records) == 2
    measured, masked = records
    assert measured.observed_date == date(2026, 7, 8)
    assert measured.hour == 0
    assert measured.administrative_dong_code == "11110515"
    assert measured.cell_id == "다사52505325"
    assert measured.total_population == Decimal("16.41")
    assert measured.total_population_raw == "16.41"
    assert measured.total_population_masked is False

    assert masked.cell_id == "다사52755375"
    assert masked.total_population is None
    assert masked.total_population_raw == "*"
    assert masked.total_population_masked is True


def test_fixture_is_really_cp949_not_utf8() -> None:
    with pytest.raises(UnicodeDecodeError):
        DERIVED_FIXTURE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("2026078,00,11110515,다사52505325,1", "YYYYMMDD"),
        ("20260230,00,11110515,다사52505325,1", "calendar date"),
        ("20260708,0,11110515,다사52505325,1", "two ASCII digits"),
        ("20260708,24,11110515,다사52505325,1", "outside 00-23"),
        ("20260708,00,1111051,다사52505325,1", "eight ASCII digits"),
        ("20260708,00,11110515,다사52515325,1", "invalid 250M격자"),
        ("20260708,00,11110515,다사52505325,", "number or '*'"),
        ("20260708,00,11110515,다사52505325,-1", "invalid 생활인구합계"),
        ("20260708,00,11110515,다사52505325,NaN", "invalid 생활인구합계"),
        ("20260708,00,11110515,다사52505325,1e2", "invalid 생활인구합계"),
        ("20260708,00,11110515,다사52505325,1_0", "invalid 생활인구합계"),
    ],
)
def test_rejects_invalid_identity_and_total_fields(
    tmp_path: Path, row: str, message: str
) -> None:
    with pytest.raises(LivingPopulationCsvError, match=message):
        tuple(iter_living_population_csv(_write_csv(tmp_path, [row])))


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("일자,시간,행정동코드,250M격자", "missing CSV columns"),
        (
            "일자,시간,행정동코드,250M격자,생활인구합계,생활인구합계",
            "duplicate CSV columns",
        ),
    ],
)
def test_rejects_missing_or_duplicate_required_columns(
    tmp_path: Path, header: str, message: str
) -> None:
    with pytest.raises(LivingPopulationCsvError, match=message):
        tuple(iter_living_population_csv(_write_csv(tmp_path, [], header=header)))


def test_accepts_extra_demographic_columns(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        ["20260708,23,11110515   ,다사52505325,16.41,*"],
        header=HEADER + ",남자0세부터9세생활인구수",
    )
    record = next(iter_living_population_csv(path))
    assert record.hour == 23
    assert record.administrative_dong_code == "11110515"


@pytest.mark.parametrize(
    "row",
    [
        "20260708,00,11110515,다사52505325",
        "20260708,00,11110515,다사52505325,1,unexpected",
    ],
)
def test_rejects_ragged_rows(tmp_path: Path, row: str) -> None:
    with pytest.raises(LivingPopulationCsvError, match="wrong number of columns"):
        tuple(iter_living_population_csv(_write_csv(tmp_path, [row])))


def test_rejects_non_cp949_input(tmp_path: Path) -> None:
    path = tmp_path / "utf8.csv"
    path.write_text(HEADER + "\n", encoding="utf-8")
    with pytest.raises(LivingPopulationCsvError, match="cp949"):
        tuple(iter_living_population_csv(path))
