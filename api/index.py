"""Read-only Vercel entry point for the current cafe-crowd snapshot."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
SNAPSHOT_DB = Path(__file__).resolve().parent / "data" / "preview.db"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# The public demo works without any external state. In production, setting a
# managed PostgreSQL DATABASE_URL switches the exact same API to live data.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{SNAPSHOT_DB}"
    os.environ.setdefault("CAFE_CROWD_SNAPSHOT", "1")

from app.main import app  # noqa: E402
