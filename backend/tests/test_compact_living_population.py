from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from scripts import compact_living_population as compact


FIXTURES = Path(__file__).parents[1] / "fixtures"
CP949_FIXTURE = FIXTURES / "living_population_minimal_cp949.csv"
HEADER = "일자,시간,행정동코드,250M격자,생활인구합계"


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join((HEADER, *rows)) + "\n", encoding="cp949")
    return path


def _allowlist(path: Path, *cell_ids: str) -> Path:
    path.write_text("\n".join(cell_ids) + "\n", encoding="utf-8")
    return path


def test_dry_run_validates_and_hashes_without_writing(tmp_path: Path) -> None:
    cells = _allowlist(tmp_path / "cells.txt", "다사52505325")
    output = tmp_path / "compact.parquet"

    result = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=output
    )

    assert result.manifest["mode"] == "dry-run"
    assert result.manifest["row_counts"] == {
        "input": 2,
        "filtered": 1,
        "masked_filtered": 0,
    }
    assert len(result.manifest["inputs"][0]["sha256"]) == 64
    assert result.manifest["output"]["sha256"] is None
    assert list(tmp_path.iterdir()) == [cells]


def test_apply_writes_normalized_parquet_and_manifest(tmp_path: Path) -> None:
    cells = _allowlist(
        tmp_path / "cells.txt", "다사52505325", "다사52755375"
    )
    output = tmp_path / "compact.parquet"

    result = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=output, apply=True
    )

    manifest_path = tmp_path / "compact.parquet.manifest.json"
    assert output.exists() and manifest_path.exists()
    assert not list(tmp_path.glob("*.part"))
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted == result.manifest
    assert persisted["query_version"] == "oa-22784-cp949-cells-v1"
    assert persisted["cell_allowlist"]["matched_cell_count"] == 2
    assert persisted["cell_allowlist"]["missing_cell_count"] == 0
    assert persisted["output"]["sha256"] == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    rows = duckdb.connect().execute(
        "SELECT date, hour, cell_id, total, masked, source_file "
        "FROM read_parquet(?) ORDER BY cell_id",
        [str(output)],
    ).fetchall()
    assert str(rows[0][0]) == "2026-07-08"
    assert rows[0][1:] == (
        0,
        "다사52505325",
        16.41,
        False,
        CP949_FIXTURE.name,
    )
    assert rows[1][2:5] == ("다사52755375", None, True)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("2026078,00,11110515,다사52505325,1", "date=1"),
        ("20260230,00,11110515,다사52505325,1", "date=1"),
        ("20260708,24,11110515,다사52505325,1", "hour=1"),
        ("20260708,00,1111051,다사52505325,1", "administrative_dong_code=1"),
        ("20260708,00,11110515,다사52515325,1", "cell_id=1"),
        ("20260708,00,11110515,다사52505325,NaN", "total=1"),
    ],
)
def test_strict_validation_rejects_invalid_source_rows(
    tmp_path: Path, row: str, message: str
) -> None:
    source = _write_csv(tmp_path / "invalid.csv", [row])
    cells = _allowlist(tmp_path / "cells.txt", "다사52755375")
    with pytest.raises(compact.LivingPopulationCompactionError, match=message):
        compact.compact_living_population(
            inputs=[source], cell_ids_path=cells, output_path=tmp_path / "out.parquet"
        )


def test_duplicate_across_inputs_fails_closed(tmp_path: Path) -> None:
    row = "20260708,00,11110515,다사52505325,1"
    first = _write_csv(tmp_path / "first.csv", [row])
    second = _write_csv(tmp_path / "second.csv", [row])
    cells = _allowlist(tmp_path / "cells.txt", "다사52505325")

    with pytest.raises(
        compact.LivingPopulationCompactionError, match="duplicate date-hour-cell"
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
        ("다사52505325", "다사53005500"),
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
    cells = _allowlist(tmp_path / "cells.txt", "다사52505325")
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
    cells = _allowlist(tmp_path / "cells.txt", "다사52755375", "다사52505325")
    first = tmp_path / "first.parquet"
    second = tmp_path / "second.parquet"
    one = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=first, apply=True
    )
    two = compact.compact_living_population(
        inputs=[CP949_FIXTURE], cell_ids_path=cells, output_path=second, apply=True
    )
    assert one.manifest["output"]["sha256"] == two.manifest["output"]["sha256"]


def test_manifest_publish_failure_rolls_back_pair_and_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = _allowlist(tmp_path / "cells.txt", "다사52505325")
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
