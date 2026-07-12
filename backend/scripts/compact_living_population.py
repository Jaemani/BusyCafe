#!/usr/bin/env python3
"""Validate and compact OA-22784 CSVs to an allowlisted Parquet extract.

The portal's CP949 monthly files are hundreds of MB each.  This offline tool
keeps only explicitly approved 250m cells while retaining source provenance.
It is dry-run by default: validation, hashing, duplicate detection, and row
counts run, but no files are created.  ``--apply`` publishes both Parquet and
its deterministic manifest through same-directory temporary files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (  # noqa: E402
    LIVING_POPULATION_COMPACT_MANIFEST_SUFFIX,
    LIVING_POPULATION_COMPACT_MISSING_CELL_AUDIT_LIMIT,
    LIVING_POPULATION_COMPACT_PARQUET_COMPRESSION,
    LIVING_POPULATION_COMPACT_PARQUET_ROW_GROUP_SIZE,
    LIVING_POPULATION_COMPACT_PART_SUFFIX,
    LIVING_POPULATION_COMPACT_QUERY_VERSION,
    LIVING_POPULATION_COMPACT_SCHEMA_VERSION,
    LIVING_POPULATION_HASH_CHUNK_BYTES,
)
from app.ingest.living_population import (  # noqa: E402
    CSV_ENCODING,
    REQUIRED_COLUMNS,
)
from app.ingest.national_grid import decode_cell_id  # noqa: E402


class LivingPopulationCompactionError(ValueError):
    """Raised when an extract cannot be produced without guessing."""


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """Dry-run/apply result; ``manifest`` is also the persisted JSON body."""

    manifest: dict[str, Any]
    output_path: Path
    manifest_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(LIVING_POPULATION_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_csv_header(path: Path) -> None:
    try:
        with path.open("r", encoding=CSV_ENCODING, newline="") as handle:
            header = next(csv.reader(handle, strict=True), None)
    except UnicodeDecodeError as exc:
        raise LivingPopulationCompactionError(
            f"{path}: CSV must be {CSV_ENCODING} encoded: {exc}"
        ) from None
    except (OSError, csv.Error) as exc:
        raise LivingPopulationCompactionError(f"{path}: cannot read CSV header: {exc}") from exc
    if header is None:
        raise LivingPopulationCompactionError(f"{path}: CSV is empty")
    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise LivingPopulationCompactionError(
            f"{path}: duplicate CSV columns: {', '.join(duplicates)}"
        )
    missing = sorted(set(REQUIRED_COLUMNS) - set(header))
    if missing:
        raise LivingPopulationCompactionError(
            f"{path}: missing CSV columns: {', '.join(missing)}"
        )


def _load_cell_ids(path: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LivingPopulationCompactionError(
            f"cannot read UTF-8 cell allowlist {path}: {exc}"
        ) from exc
    cell_ids: list[str] = []
    for line_number, raw in enumerate(lines, start=1):
        cell_id = raw.strip()
        if not cell_id:
            continue
        try:
            cell_ids.append(decode_cell_id(cell_id).cell_id)
        except ValueError as exc:
            raise LivingPopulationCompactionError(
                f"{path}:{line_number}: invalid cell id: {exc}"
            ) from None
    if not cell_ids:
        raise LivingPopulationCompactionError("cell allowlist must not be empty")
    duplicates = sorted({item for item in cell_ids if cell_ids.count(item) > 1})
    if duplicates:
        raise LivingPopulationCompactionError(
            "duplicate cell ids in allowlist: " + ", ".join(duplicates)
        )
    return tuple(sorted(cell_ids))


def _preflight_paths(
    inputs: list[Path], cell_ids_path: Path, output_path: Path, manifest_path: Path
) -> tuple[list[Path], Path, Path, Path]:
    resolved_inputs = sorted({path.resolve() for path in inputs}, key=str)
    if len(resolved_inputs) != len(inputs):
        raise LivingPopulationCompactionError("input CSV paths must be unique")
    if not resolved_inputs:
        raise LivingPopulationCompactionError("at least one input CSV is required")
    for path in resolved_inputs:
        if not path.is_file():
            raise LivingPopulationCompactionError(f"input CSV does not exist: {path}")
        _validate_csv_header(path)

    cell_ids_path = cell_ids_path.resolve()
    if not cell_ids_path.is_file():
        raise LivingPopulationCompactionError(
            f"cell allowlist does not exist: {cell_ids_path}"
        )
    output_path = output_path.resolve()
    manifest_path = manifest_path.resolve()
    if output_path.suffix.lower() != ".parquet":
        raise LivingPopulationCompactionError("output path must end in .parquet")
    protected = {output_path, manifest_path}
    if any(path in protected for path in (*resolved_inputs, cell_ids_path)):
        raise LivingPopulationCompactionError("inputs and allowlist cannot be output paths")

    output_part = output_path.with_name(
        output_path.name + LIVING_POPULATION_COMPACT_PART_SUFFIX
    )
    manifest_part = manifest_path.with_name(
        manifest_path.name + LIVING_POPULATION_COMPACT_PART_SUFFIX
    )
    for path in (output_path, manifest_path, output_part, manifest_part):
        if path.exists():
            raise LivingPopulationCompactionError(
                f"refusing to overwrite existing output or partial file: {path}"
            )
    return resolved_inputs, cell_ids_path, output_part, manifest_part


def _create_source_view(connection: duckdb.DuckDBPyConnection, inputs: list[Path]) -> None:
    input_list = ", ".join(_sql_string(str(path)) for path in inputs)
    connection.execute(
        f"""
        CREATE TEMP VIEW source_rows AS
        SELECT
            trim("일자") AS date_raw,
            trim("시간") AS hour_raw,
            trim("행정동코드") AS administrative_dong_code,
            trim("250M격자") AS cell_id,
            trim("생활인구합계") AS total_raw,
            filename AS source_path
        FROM read_csv(
            [{input_list}],
            encoding = 'cp949',
            all_varchar = true,
            header = true,
            union_by_name = true,
            filename = true,
            ignore_errors = false,
            null_padding = false
        )
        """
    )


def _validate_rows(connection: duckdb.DuckDBPyConnection) -> tuple[int, dict[str, int]]:
    # Validation is deliberately set-based: every source row is checked before
    # the allowlist filter, so malformed data cannot silently hide outside the
    # current experiment cells.
    row = connection.execute(
        """
        SELECT
            count(*) AS total_rows,
            count(*) FILTER (WHERE NOT coalesce(
                regexp_full_match(date_raw, '[0-9]{8}')
                AND try_strptime(date_raw, '%Y%m%d') IS NOT NULL, false
            )) AS invalid_date,
            count(*) FILTER (WHERE NOT coalesce(
                regexp_full_match(hour_raw, '[0-9]{2}')
                AND try_cast(hour_raw AS INTEGER) BETWEEN 0 AND 23, false
            )) AS invalid_hour,
            count(*) FILTER (WHERE NOT coalesce(
                regexp_full_match(administrative_dong_code, '[0-9]{8}'), false
            )) AS invalid_admin_code,
            count(*) FILTER (WHERE NOT coalesce(
                substr(cell_id, 1, 1) IN ('가','나','다','라','마','바','사')
                AND substr(cell_id, 2, 1) IN ('가','나','다','라','마','바','사','아')
                AND regexp_full_match(substr(cell_id, 3), '[0-9]{8}')
                AND try_cast(substr(cell_id, 3, 4) AS INTEGER) % 25 = 0
                AND try_cast(substr(cell_id, 7, 4) AS INTEGER) % 25 = 0,
                false
            )) AS invalid_cell_id,
            count(*) FILTER (WHERE NOT coalesce(
                total_raw = '*'
                OR (
                    regexp_full_match(total_raw, '[0-9]+(?:\\.[0-9]+)?')
                    AND try_cast(total_raw AS DOUBLE) IS NOT NULL
                    AND isfinite(try_cast(total_raw AS DOUBLE))
                    AND try_cast(total_raw AS DOUBLE) >= 0
                ), false
            )) AS invalid_total
        FROM source_rows
        """
    ).fetchone()
    assert row is not None
    names = (
        "date",
        "hour",
        "administrative_dong_code",
        "cell_id",
        "total",
    )
    invalid = {name: int(value) for name, value in zip(names, row[1:], strict=True)}
    if any(invalid.values()):
        detail = ", ".join(f"{name}={count}" for name, count in invalid.items() if count)
        raise LivingPopulationCompactionError(f"strict row validation failed: {detail}")
    return int(row[0]), invalid


def _reject_duplicate_observations(connection: duckdb.DuckDBPyConnection) -> None:
    duplicate = connection.execute(
        """
        SELECT date_raw, hour_raw, cell_id, count(*) AS n
        FROM source_rows
        GROUP BY date_raw, hour_raw, cell_id
        HAVING count(*) > 1
        ORDER BY date_raw, hour_raw, cell_id
        LIMIT 1
        """
    ).fetchone()
    if duplicate is not None:
        raise LivingPopulationCompactionError(
            "duplicate date-hour-cell observation: "
            f"{duplicate[0]} {duplicate[1]} {duplicate[2]} ({duplicate[3]} rows)"
        )


def _reserve(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)


def compact_living_population(
    *,
    inputs: list[Path],
    cell_ids_path: Path,
    output_path: Path,
    apply: bool = False,
) -> CompactionResult:
    """Validate all rows and optionally publish one allowlisted extract."""

    manifest_path = output_path.with_name(
        output_path.name + LIVING_POPULATION_COMPACT_MANIFEST_SUFFIX
    )
    inputs, cell_ids_path, output_part, manifest_part = _preflight_paths(
        inputs, cell_ids_path, output_path, manifest_path
    )
    output_path = output_path.resolve()
    manifest_path = manifest_path.resolve()
    cell_ids = _load_cell_ids(cell_ids_path)
    input_metadata = [
        {
            "path": str(path),
            "source_file": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in inputs
    ]

    connection = duckdb.connect(":memory:")
    try:
        _create_source_view(connection, inputs)
        total_rows, _ = _validate_rows(connection)
        _reject_duplicate_observations(connection)
        connection.execute("CREATE TEMP TABLE allowed_cells(cell_id VARCHAR PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO allowed_cells VALUES (?)", [(cell_id,) for cell_id in cell_ids]
        )
        connection.execute(
            """
            CREATE TEMP TABLE selected_rows AS
            SELECT
                cast(strptime(date_raw, '%Y%m%d') AS DATE) AS date,
                cast(hour_raw AS UTINYINT) AS hour,
                cell_id,
                CASE WHEN total_raw = '*' THEN NULL
                     ELSE cast(total_raw AS DOUBLE) END AS total,
                total_raw = '*' AS masked,
                parse_filename(source_path) AS source_file
            FROM source_rows
            WHERE cell_id IN (SELECT cell_id FROM allowed_cells)
            ORDER BY date, hour, cell_id, source_file
            """
        )
        missing_cells = [
            row[0]
            for row in connection.execute(
                """
                SELECT allowed.cell_id
                FROM allowed_cells AS allowed
                LEFT JOIN (
                    SELECT DISTINCT cell_id FROM selected_rows
                ) AS matched USING (cell_id)
                WHERE matched.cell_id IS NULL
                ORDER BY allowed.cell_id
                """
            ).fetchall()
        ]
        matched_cell_count = len(cell_ids) - len(missing_cells)
        if missing_cells:
            sample = missing_cells[:LIVING_POPULATION_COMPACT_MISSING_CELL_AUDIT_LIMIT]
            suffix = "" if len(sample) == len(missing_cells) else ", ..."
            raise LivingPopulationCompactionError(
                "allowlist cells missing from source "
                f"({len(missing_cells)}/{len(cell_ids)}): {', '.join(sample)}{suffix}"
            )
        filtered_rows, masked_rows = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE masked) FROM selected_rows"
        ).fetchone()
        rows_by_source = dict(
            connection.execute(
                "SELECT source_path, count(*) FROM source_rows GROUP BY source_path"
            ).fetchall()
        )
        for item in input_metadata:
            item["row_count"] = int(rows_by_source.get(item["path"], 0))

        output_schema = [
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
            for row in connection.execute("DESCRIBE selected_rows").fetchall()
        ]
        manifest: dict[str, Any] = {
            "schema_version": LIVING_POPULATION_COMPACT_SCHEMA_VERSION,
            "query_version": LIVING_POPULATION_COMPACT_QUERY_VERSION,
            "duckdb_version": duckdb.__version__,
            "mode": "apply" if apply else "dry-run",
            "inputs": input_metadata,
            "cell_allowlist": {
                "path": str(cell_ids_path),
                "size_bytes": cell_ids_path.stat().st_size,
                "sha256": _sha256(cell_ids_path),
                "cell_count": len(cell_ids),
                "matched_cell_count": matched_cell_count,
                "missing_cell_count": len(missing_cells),
            },
            "row_counts": {
                "input": total_rows,
                "filtered": int(filtered_rows),
                "masked_filtered": int(masked_rows),
            },
            "output": {
                "path": str(output_path),
                "schema": output_schema,
                "size_bytes": None,
                "sha256": None,
            },
        }
        if not apply:
            return CompactionResult(manifest, output_path, manifest_path)

        output_reserved = False
        manifest_reserved = False
        output_published = False
        try:
            _reserve(output_part)
            output_reserved = True
            connection.execute(
                f"""
                COPY selected_rows TO {_sql_string(str(output_part))}
                (FORMAT PARQUET,
                 COMPRESSION {_sql_string(LIVING_POPULATION_COMPACT_PARQUET_COMPRESSION)},
                 ROW_GROUP_SIZE {LIVING_POPULATION_COMPACT_PARQUET_ROW_GROUP_SIZE})
                """
            )
            manifest["output"]["size_bytes"] = output_part.stat().st_size
            manifest["output"]["sha256"] = _sha256(output_part)

            _reserve(manifest_part)
            manifest_reserved = True
            manifest_part.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.link(output_part, output_path)
            output_published = True
            try:
                os.link(manifest_part, manifest_path)
            except OSError:
                output_path.unlink(missing_ok=True)
                output_published = False
                raise
        finally:
            if output_reserved:
                output_part.unlink(missing_ok=True)
            if manifest_reserved:
                manifest_part.unlink(missing_ok=True)
            if output_published and not manifest_path.exists():
                output_path.unlink(missing_ok=True)
        return CompactionResult(manifest, output_path, manifest_path)
    except duckdb.Error as exc:
        raise LivingPopulationCompactionError(f"DuckDB compaction failed: {exc}") from exc
    finally:
        connection.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--cell-ids", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="publish output (default: dry-run)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = compact_living_population(
            inputs=args.input,
            cell_ids_path=args.cell_ids,
            output_path=args.output,
            apply=args.apply,
        )
        print(json.dumps(result.manifest, ensure_ascii=False, indent=2, sort_keys=True))
        if not args.apply:
            print("dry-run: pass --apply to publish", file=sys.stderr)
        return 0
    except (LivingPopulationCompactionError, OSError) as exc:
        print(f"living-population compaction failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
