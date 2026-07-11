"""Small deterministic geospatial primitives shared by ingestion and scoring."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Final


# IUGG mean Earth radius. This is a physical constant, not a tuning parameter.
EARTH_RADIUS_M: Final = 6_371_008.8


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in metres between two WGS84 coordinates."""

    for name, value, lower, upper in (
        ("lat1", lat1, -90.0, 90.0),
        ("lng1", lng1, -180.0, 180.0),
        ("lat2", lat2, -90.0, 90.0),
        ("lng2", lng2, -180.0, 180.0),
    ):
        if not lower <= value <= upper:
            raise ValueError(f"{name} must be between {lower:g} and {upper:g}")

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = lat2_rad - lat1_rad
    delta_lng = radians(lng2 - lng1)
    haversine = (
        sin(delta_lat / 2.0) ** 2
        + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lng / 2.0) ** 2
    )
    # Guard against the tiny floating-point overshoot possible at antipodes.
    central_angle = 2.0 * asin(sqrt(min(1.0, max(0.0, haversine))))
    return EARTH_RADIUS_M * central_angle
