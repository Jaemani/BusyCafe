"""Exercise the real API against an ephemeral SQLite DB, never production."""

from __future__ import annotations

import os
from tempfile import TemporaryDirectory


def main() -> None:
    # Set before importing app modules so inherited production configuration
    # cannot select a real database or accept user submissions.
    with TemporaryDirectory(prefix="busy-cafe-readiness-") as directory:
        os.environ["DATABASE_URL"] = f"sqlite:///{directory}/smoke.db"
        os.environ["USER_CONTRIBUTIONS_ENABLED"] = "false"
        from fastapi.testclient import TestClient

        from app.database import engine
        from app.main import app
        from app.models import Base

        try:
            Base.metadata.create_all(engine)
            with TestClient(app) as client:
                health = client.get("/api/health")
                cafes = client.get("/api/cafes?bbox=126.91,37.54,126.94,37.57")
                assert health.status_code == 200
                assert health.json()["cafes_count"] == 0
                assert cafes.status_code == 200
                assert cafes.json() == []
            print("PASS isolated SQLite API: health=200, cafes=200, empty catalog")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
