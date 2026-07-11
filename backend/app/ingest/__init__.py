"""Ingestion helpers for turning verified external data into domain records."""

from app.ingest.hotspot_master import (
    HotspotMasterError,
    HotspotMasterRecord,
    load_hotspot_master,
)

__all__ = [
    "HotspotMasterError",
    "HotspotMasterRecord",
    "load_hotspot_master",
]
