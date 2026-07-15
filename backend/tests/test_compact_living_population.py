from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from scripts import compact_living_population as compact


FIXTURES = Path(__file__).parents[1] / "fixtures"
CP949_FIXTURE = FIXTURES / "living_population_minimal_cp949.csv"
HEADER = "일자,시간,행정동코드,250M격자,생활인구합계"
CELL_A = "다사52505325"
CELL_B = "다사52755375"


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join((HEADER, *rows)) + "\n", encoding="cp949")
    return path


def _allowlist(path: Path, *cell_ids: str) -> Path:
    path.write_text("\n".join(cell_ids) + "\n", encoding="utf-8")
    return path


def _compact_rows(
    tmp_path: Path,
    rows: list[str],
    *,
    source_name: str = "source.csv",
) -> tuple[compact.CompactionResult, list[tuple[Any, ...]]]:
    source = _write_csv(tmp_path / source_name, rows)
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)
    output = tmp_path / "compact.parquet"
    result = compact.compact_living_population(
        inputs=[source], cell_ids_path=cells, output_path=output, apply=True
    )
    parquet_rows = duckdb.connect().execute(
        "SELECT date, hour, cell_id, known_total, fragment_count, "
        "masked_fragment_count, fragments_json FROM read_parquet(?)",
        [str(output)],
    ).fetchall()
    return result, parquet_rows


def test_dry_run_validates_and_hashes_without_writing(tmp_path: Path) -> None:
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)
    output = tmp_path / "compact.parquet"

    result = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=output
    )

    assert result.manifest["mode"] == "dry-run"
    assert result.manifest["row_counts"] == {
        "input": 2,
        "fragment_rows_filtered": 1,
        "cell_observations_filtered": 1,
        "masked_fragments_filtered": 0,
        "multi_fragment_cell_observations": 0,
        "partially_masked_cell_observations": 0,
        "all_masked_cell_observations": 0,
        "max_fragments_per_cell": 1,
    }
    assert len(result.manifest["inputs"][0]["sha256"]) == 64
    assert result.manifest["output"]["sha256"] is None
    assert list(tmp_path.iterdir()) == [cells]


def test_numeric_fragments_aggregate_exactly(tmp_path: Path) -> None:
    _, rows = _compact_rows(
        tmp_path,
        [
            f"20260630,00,11110515,{CELL_A},1.25",
            f"20260630,00,11110530,{CELL_A},2.50",
        ],
    )

    assert len(rows) == 1
    assert rows[0][3:6] == (Decimal("3.75000"), 2, 0)
    fragments = json.loads(rows[0][6])
    assert [fragment["known_value"] for fragment in fragments] == [
        "1.25000",
        "2.50000",
    ]


def test_partial_mask_preserves_known_total_and_fragment(tmp_path: Path) -> None:
    result, rows = _compact_rows(
        tmp_path,
        [
            f"20260630,00,11110515,{CELL_A},7.125",
            f"20260630,00,11110530,{CELL_A},*",
        ],
    )

    assert rows[0][3:6] == (Decimal("7.12500"), 2, 1)
    fragments = json.loads(rows[0][6])
    assert fragments[0]["masked"] is False
    assert fragments[1]["masked"] is True
    assert fragments[1]["known_value"] is None
    assert result.manifest["row_counts"]["partially_masked_cell_observations"] == 1
    assert result.manifest["row_counts"]["all_masked_cell_observations"] == 0


def test_all_masked_fragments_yield_exact_zero(tmp_path: Path) -> None:
    result, rows = _compact_rows(
        tmp_path,
        [
            f"20260630,00,11110515,{CELL_A},*",
            f"20260630,00,11110530,{CELL_A},*",
        ],
    )

    assert rows[0][3:6] == (Decimal("0.00000"), 2, 2)
    assert [fragment["total_raw"] for fragment in json.loads(rows[0][6])] == ["*", "*"]
    assert result.manifest["row_counts"]["all_masked_cell_observations"] == 1
    assert result.manifest["row_counts"]["partially_masked_cell_observations"] == 0


def test_trailing_decimal_point_is_numeric_and_raw_token_survives(
    tmp_path: Path,
) -> None:
    _, rows = _compact_rows(
        tmp_path,
        [f"20260630,00,11110515,{CELL_A},540."],
    )

    assert rows[0][3] == Decimal("540.00000")
    fragment = json.loads(rows[0][6])[0]
    assert fragment["known_value"] == "540.00000"
    assert fragment["total_raw"] == "540."


def test_same_cell_observation_with_different_admin_codes_is_allowed(
    tmp_path: Path,
) -> None:
    _, rows = _compact_rows(
        tmp_path,
        [
            f"20260630,00,11110515,{CELL_A},1",
            f"20260630,00,11110530,{CELL_A},2",
        ],
    )

    assert rows[0][3:6] == (Decimal("3.00000"), 2, 0)
    fragments = json.loads(rows[0][6])
    assert [fragment["administrative_dong_code"] for fragment in fragments] == [
        "11110515",
        "11110530",
    ]


def test_fragment_invariants_and_order_are_deterministic(tmp_path: Path) -> None:
    _, rows = _compact_rows(
        tmp_path,
        [
            f"20260630,00,11110530,{CELL_A},*",
            f"20260630,00,11110545,{CELL_A},2.25000",
            f"20260630,00,11110515,{CELL_A},1.125",
        ],
        source_name="fragments.csv",
    )

    known_total, fragment_count, masked_count, fragments_json = rows[0][3:7]
    fragments = json.loads(fragments_json)
    assert len(fragments) == fragment_count == 3
    assert sum(fragment["masked"] for fragment in fragments) == masked_count == 1
    assert sum(
        Decimal(fragment["known_value"])
        for fragment in fragments
        if fragment["known_value"] is not None
    ) == known_total == Decimal("3.37500")
    assert [fragment["administrative_dong_code"] for fragment in fragments] == [
        "11110515",
        "11110530",
        "11110545",
    ]
    assert {fragment["source_file"] for fragment in fragments} == {"fragments.csv"}


def test_apply_writes_exact_v2_schema_and_manifest(tmp_path: Path) -> None:
    cells = _allowlist(tmp_path / "cells.txt", CELL_A, CELL_B)
    output = tmp_path / "compact.parquet"

    result = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=output, apply=True
    )

    manifest_path = tmp_path / "compact.parquet.manifest.json"
    assert output.exists() and manifest_path.exists()
    assert not list(tmp_path.glob("*.part"))
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted == result.manifest
    assert persisted["schema_version"] == 2
    assert persisted["query_version"] == "oa-22784-cp949-cell-fragments-json-v2"
    assert persisted["cell_allowlist"]["matched_cell_count"] == 2
    assert persisted["cell_allowlist"]["missing_cell_count"] == 0
    assert persisted["output"]["sha256"] == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    assert persisted["output"]["schema"] == [
        {"name": "date", "type": "DATE", "nullable": True},
        {"name": "hour", "type": "UTINYINT", "nullable": True},
        {"name": "cell_id", "type": "VARCHAR", "nullable": True},
        {"name": "known_total", "type": "DECIMAL(38,5)", "nullable": True},
        {"name": "fragment_count", "type": "UINTEGER", "nullable": True},
        {
            "name": "masked_fragment_count",
            "type": "UINTEGER",
            "nullable": True,
        },
        {
            "name": "fragments_json",
            "type": "VARCHAR",
            "nullable": True,
        },
    ]
    rows = duckdb.connect().execute(
        "SELECT cell_id, known_total, fragment_count, masked_fragment_count, "
        "fragments_json FROM read_parquet(?) ORDER BY cell_id",
        [str(output)],
    ).fetchall()
    assert rows[0][0:4] == (CELL_A, Decimal("16.41000"), 1, 0)
    assert json.loads(rows[0][4])[0]["source_file"] == CP949_FIXTURE.name
    assert rows[1][0:4] == (CELL_B, Decimal("0.00000"), 1, 1)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (f"2026078,00,11110515,{CELL_A},1", "일자"),
        (f"20260230,00,11110515,{CELL_A},1", "일자"),
        (f"20260708,24,11110515,{CELL_A},1", "시간"),
        (f"20260708,00,1111051,{CELL_A},1", "행정동코드"),
        ("20260708,00,11110515,다사52515325,1", "250M격자"),
        (f"20260708,00,11110515,{CELL_A},NaN", "생활인구합계"),
    ],
)
def test_strict_validation_rejects_invalid_source_rows(
    tmp_path: Path, row: str, message: str
) -> None:
    source = _write_csv(tmp_path / "invalid.csv", [row])
    cells = _allowlist(tmp_path / "cells.txt", CELL_B)
    with pytest.raises(compact.LivingPopulationCompactionError, match=message):
        compact.compact_living_population(
            inputs=[source], cell_ids_path=cells, output_path=tmp_path / "out.parquet"
        )


def test_fraction_scale_above_five_is_rejected_without_rounding(tmp_path: Path) -> None:
    source = _write_csv(
        tmp_path / "excess-scale.csv",
        [f"20260708,00,11110515,{CELL_A},1.000001"],
    )
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)

    with pytest.raises(
        compact.LivingPopulationCompactionError, match="fraction_scale=1"
    ):
        compact.compact_living_population(
            inputs=[source], cell_ids_path=cells, output_path=tmp_path / "out.parquet"
        )


def test_normalized_temp_directory_is_removed_after_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_csv(
        tmp_path / "invalid.csv",
        [f"20260708,24,11110515,{CELL_A},1"],
    )
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)
    real_temporary_directory = compact.tempfile.TemporaryDirectory
    created: list[Path] = []

    def local_temporary_directory(*, prefix: str) -> Any:
        temporary_directory = real_temporary_directory(prefix=prefix, dir=tmp_path)
        created.append(Path(temporary_directory.name))
        return temporary_directory

    monkeypatch.setattr(
        compact.tempfile, "TemporaryDirectory", local_temporary_directory
    )
    with pytest.raises(compact.LivingPopulationCompactionError, match="시간"):
        compact.compact_living_population(
            inputs=[source], cell_ids_path=cells, output_path=tmp_path / "out.parquet"
        )

    assert len(created) == 1
    assert not created[0].exists()


def test_duplicate_four_key_across_inputs_fails_closed(tmp_path: Path) -> None:
    row = f"20260708,00,11110515,{CELL_A},1"
    first = _write_csv(tmp_path / "first.csv", [row])
    second = _write_csv(tmp_path / "second.csv", [row])
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)

    with pytest.raises(
        compact.LivingPopulationCompactionError,
        match="duplicate date-hour-admin-cell fragment",
    ):
        compact.compact_living_population(
            inputs=[first, second],
            cell_ids_path=cells,
            output_path=tmp_path / "out.parquet",
        )


def test_duplicate_input_basenames_from_different_directories_fail(
    tmp_path: Path,
) -> None:
    first = _write_csv(
        tmp_path / "a" / "same.csv", [f"20260708,00,11110515,{CELL_A},1"]
    )
    second = _write_csv(
        tmp_path / "b" / "same.csv", [f"20260708,00,11110530,{CELL_A},2"]
    )
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)

    with pytest.raises(
        compact.LivingPopulationCompactionError,
        match="source_file basenames must be unique: same.csv",
    ):
        compact.compact_living_population(
            inputs=[first, second],
            cell_ids_path=cells,
            output_path=tmp_path / "out.parquet",
        )


@pytest.mark.parametrize(
    "cell_ids",
    [
        ("다사53005500",),
        (CELL_A, "다사53005500"),
    ],
    ids=["zero-match", "partial-match"],
)
def test_all_allowlisted_cells_must_exist_in_source(
    tmp_path: Path, cell_ids: tuple[str, ...]
) -> None:
    cells = _allowlist(tmp_path / "cells.txt", *cell_ids)
    output = tmp_path / "out.parquet"

    with pytest.raises(
        compact.LivingPopulationCompactionError,
        match="allowlist cells missing from source",
    ):
        compact.compact_living_population(
            inputs=[CP949_FIXTURE],
            cell_ids_path=cells,
            output_path=output,
            apply=True,
        )

    assert not output.exists()
    assert not (tmp_path / "out.parquet.manifest.json").exists()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("existing", ["output", "manifest", "output_part", "manifest_part"])
def test_refuses_existing_outputs_and_parts(tmp_path: Path, existing: str) -> None:
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)
    output = tmp_path / "out.parquet"
    paths = {
        "output": output,
        "manifest": tmp_path / "out.parquet.manifest.json",
        "output_part": tmp_path / "out.parquet.part",
        "manifest_part": tmp_path / "out.parquet.manifest.json.part",
    }
    paths[existing].write_bytes(b"preserve")

    with pytest.raises(compact.LivingPopulationCompactionError, match="overwrite"):
        compact.compact_living_population(
            inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=output, apply=True
        )
    assert paths[existing].read_bytes() == b"preserve"


def test_output_is_deterministic_for_same_source_and_allowlist(tmp_path: Path) -> None:
    cells = _allowlist(tmp_path / "cells.txt", CELL_B, CELL_A)
    first = tmp_path / "first.parquet"
    second = tmp_path / "second.parquet"
    one = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=first, apply=True
    )
    two = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=second, apply=True
    )
    assert one.manifest["output"]["sha256"] == two.manifest["output"]["sha256"]


def test_reversed_input_argument_order_produces_same_parquet_sha(
    tmp_path: Path,
) -> None:
    first_source = _write_csv(
        tmp_path / "a.csv", [f"20260708,00,11110530,{CELL_A},2"]
    )
    second_source = _write_csv(
        tmp_path / "b.csv", [f"20260708,00,11110515,{CELL_A},1"]
    )
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)
    first_output = tmp_path / "first.parquet"
    second_output = tmp_path / "second.parquet"

    first = compact.compact_living_population(
        inputs=[first_source, second_source],
        cell_ids_path=cells,
        output_path=first_output,
        apply=True,
    )
    second = compact.compact_living_population(
        inputs=[second_source, first_source],
        cell_ids_path=cells,
        output_path=second_output,
        apply=True,
    )

    assert first.manifest["output"]["sha256"] == second.manifest["output"]["sha256"]


def test_manifest_publish_failure_rolls_back_pair_and_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = _allowlist(tmp_path / "cells.txt", CELL_A)
    output = tmp_path / "out.parquet"
    real_link = compact.os.link
    calls = 0

    def fail_second_link(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated manifest publish failure")
        real_link(source, destination)

    monkeypatch.setattr(compact.os, "link", fail_second_link)
    with pytest.raises(OSError, match="simulated"):
        compact.compact_living_population(
            inputs=[CP949_FIXTURE],
            cell_ids_path=cells,
            output_path=output,
            apply=True,
        )

    assert not output.exists()
    assert not (tmp_path / "out.parquet.manifest.json").exists()
    assert not list(tmp_path.glob("*.part"))
