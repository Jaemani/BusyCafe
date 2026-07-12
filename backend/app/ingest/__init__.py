"""Ingestion helpers for turning verified external data into domain records."""

from app.ingest.hotspot_master import (
    HotspotMasterError,
    HotspotGeometryRecord,
    HotspotMasterRecord,
    load_hotspot_geometry_master,
    load_hotspot_master,
)

__all__ = [
    "HotspotMasterError",
    "HotspotGeometryRecord",
    "HotspotMasterRecord",
    "load_hotspot_geometry_master",
    "load_hotspot_master",
]
