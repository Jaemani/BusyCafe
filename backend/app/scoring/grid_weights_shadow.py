"""Deterministic hotspot-polygon × 250m-cell weights for offline research.

This module is deliberately shadow-only.  It derives cell polygons from the
currently sample-verified national-grid decoder, intersects them with the
already verified official hotspot polygons, and returns no database writes or
public scores.  Promotion is forbidden until the inferred cell boundaries are
compared exhaustively with an authoritative 250m boundary file.

For a cell ``i`` and hotspot ``h`` the weight is::

    intersection(h, i).area / i.area

The living-population value is a count for the whole cell, so this fraction
allocates the count under the explicit uniform-within-cell assumption.  Area
ratios are calculated in the same local WGS84 plane for numerator and
denominator; the common longitude/latitude scale cancels at 250m resolution.
``intersection_area_m2`` uses the authoritative projected cell size
(``250m × 250m``) times that ratio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from app.ingest.hotspot_master import HotspotGeometryRecord
from app.ingest.national_grid import (
    CELL_GEOMETRY_VERSION,
    CELL_SIZE_M,
    cell_wgs84_corners,
)


@dataclass(frozen=True, slots=True)
class HotspotCellWeight:
    """One positive-area hotspot/cell intersection with full provenance."""

    area_cd: str
    hotspot_geometry_version: str
    cell_id: str
    cell_geometry_version: str
    cell_fraction: float
    intersection_area_m2: float


def _cell_polygon(cell_id: str) -> Polygon:
    corners = cell_wgs84_corners(cell_id)
    polygon = Polygon((lng, lat) for lat, lng in corners)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f"decoded cell geometry is invalid: {cell_id!r}")
    return polygon


def _validate_hotspot_geometry(record: HotspotGeometryRecord) -> None:
    geometry = record.geometry
    if not record.area_cd or not record.geometry_version:
        raise ValueError("hotspot area_cd and geometry_version must be non-empty")
    if not isinstance(geometry, BaseGeometry):
        raise ValueError(f"{record.area_cd} geometry must be a Shapely geometry")
    if (
        geometry.geom_type not in {"Polygon", "MultiPolygon"}
        or geometry.is_empty
        or not geometry.is_valid
    ):
        raise ValueError(
            f"{record.area_cd} geometry must be a normalized valid polygon"
        )
    min_lng, min_lat, max_lng, max_lat = geometry.bounds
    if not (
        -180.0 <= min_lng <= max_lng <= 180.0
        and -90.0 <= min_lat <= max_lat <= 90.0
    ):
        raise ValueError(f"{record.area_cd} geometry is outside WGS84 bounds")


def generate_hotspot_cell_weights_shadow(
    hotspots: Sequence[HotspotGeometryRecord],
    cell_ids: Iterable[str],
) -> tuple[HotspotCellWeight, ...]:
    """Build stable positive-area weights without I/O or score mutation.

    Duplicate hotspot codes and duplicate normalized cell IDs fail closed.
    Boundary-only touches do not receive a zero weight.  Output ordering is
    always ``(area_cd, cell_id)`` regardless of input order.
    """

    hotspot_by_code: dict[str, HotspotGeometryRecord] = {}
    for hotspot in hotspots:
        _validate_hotspot_geometry(hotspot)
        if hotspot.area_cd in hotspot_by_code:
            raise ValueError(f"duplicate hotspot area_cd: {hotspot.area_cd}")
        hotspot_by_code[hotspot.area_cd] = hotspot

    cell_by_id: dict[str, Polygon] = {}
    for raw_cell_id in cell_ids:
        # ``cell_wgs84_corners`` validates type/format and strips only for
        # decoding.  Preserve one canonical ID and detect whitespace aliases.
        polygon = _cell_polygon(raw_cell_id)
        canonical_cell_id = raw_cell_id.strip()
        if canonical_cell_id in cell_by_id:
            raise ValueError(f"duplicate cell_id: {canonical_cell_id}")
        cell_by_id[canonical_cell_id] = polygon

    ordered_cells = sorted(cell_by_id.items())
    cell_geometries = [geometry for _, geometry in ordered_cells]
    if not hotspot_by_code or not cell_geometries:
        return ()
    tree = STRtree(cell_geometries)

    results: list[HotspotCellWeight] = []
    for area_cd in sorted(hotspot_by_code):
        hotspot = hotspot_by_code[area_cd]
        candidate_indexes = sorted(
            int(index)
            for index in tree.query(hotspot.geometry, predicate="intersects")
        )
        for index in candidate_indexes:
            cell_id, cell_geometry = ordered_cells[index]
            intersection_area = hotspot.geometry.intersection(cell_geometry).area
            if intersection_area <= 0:
                continue
            fraction = intersection_area / cell_geometry.area
            # GEOS can exceed 1 by a few ulps for coincident boundaries.  Larger
            # excess means invalid topology or a broken area convention.
            if fraction > 1.0 + 1e-12:
                raise ValueError(
                    f"intersection fraction exceeds one: {area_cd}/{cell_id}"
                )
            fraction = min(1.0, fraction)
            results.append(
                HotspotCellWeight(
                    area_cd=area_cd,
                    hotspot_geometry_version=hotspot.geometry_version,
                    cell_id=cell_id,
                    cell_geometry_version=CELL_GEOMETRY_VERSION,
                    cell_fraction=fraction,
                    intersection_area_m2=fraction * CELL_SIZE_M**2,
                )
            )
    return tuple(results)
