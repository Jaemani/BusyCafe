"""Recompute all cached cafe estimates from already stored snapshots."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.database import create_db_engine
from app.scoring.engine import materialize_all


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override DATABASE_URL")
    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = materialize_all(session)
        print(
            "Cafe score materialization\n"
            f"cafes: {report.cafes}\n"
            f"covered/fringe/uncovered: "
            f"{report.covered}/{report.fringe}/{report.uncovered}"
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
