"""Decode Korean national-grid ``CELL_ID`` values into WGS84 geometry.

The Seoul 250m living-population feed (service ``Se250MSpopLocalResd``,
adopted in ADR-0009) labels every cell with a national point number
(국가지점번호) such as ``다사52505325``.  This module turns that pure string
into the cell's WGS84 bounds and centre with no I/O and no dependency on the
config module — the grid is a fixed geodetic fact, not a tuning parameter.

Encoding (행정안전부 국가지점번호, 도로명주소법 시행령 제37조):

* Two hangul letters name a 100km × 100km square.  The reference corner
  ``가가 0000 0000`` sits at UTM-K (EPSG:5179) easting 700,000 m / northing
  1,300,000 m — 300 km west and 700 km south of the projection's virtual
  origin (1,000,000, 2,000,000).  The first letter counts 100km squares east
  ('가'→'사'), the second counts them north ('가'→'아').  Seoul falls in
  ``다사``.
* The eight digits are two 4-digit fields: easting then northing, each the
  offset **inside** the 100km square in units of 10 m.  A 250m cell therefore
  steps the digits by 25 (250 m ÷ 10 m).  The number addresses the cell's
  south-west corner.

The projected corner is then inverted through the UTM-K Transverse Mercator
(GRS80) back to latitude/longitude.  EPSG:5179's datum (Korea 2000, GRS80,
geocentric) coincides with WGS84 to well under a millimetre for our purposes,
so no datum shift is applied and the result is reported directly as WGS84.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, degrees, radians, sin, sqrt, tan
from typing import Final

# --- National grid constants (fixed geodetic facts, never tuned) -------------

# EPSG:5179 easting/northing of the ``가가 0000 0000`` reference corner.
NATIONAL_GRID_ORIGIN_E: Final = 700_000.0
NATIONAL_GRID_ORIGIN_N: Final = 1_300_000.0
# Side of one lettered square and the digit resolution inside it.
HUNDRED_KM_M: Final = 100_000.0
DIGIT_UNIT_M: Final = 10.0
# This feed publishes the 250m grid; the digit fields must land on it.
CELL_SIZE_M: Final = 250.0
# Provenance label for geometry inferred from ``CELL_ID``.  ``shadow`` and
# ``unverified`` are intentional: the arithmetic decoder has passed sample
# checks, but an authority boundary-file comparison is still outstanding.
CELL_GEOMETRY_VERSION: Final = "national-grid-250m-shadow-unverified-v1"
# Letters assigned east (first hangul) and north (second hangul).  Korea only
# spans these; anything else is not a national-grid cell we accept.
EASTING_LETTERS: Final = "가나다라마바사"
NORTHING_LETTERS: Final = "가나다라마바사아"

# --- UTM-K (EPSG:5179) Transverse Mercator parameters, GRS80 ellipsoid -------

UTMK_A: Final = 6_378_137.0  # GRS80 semi-major axis (m)
UTMK_INV_F: Final = 298.257_222_101  # GRS80 inverse flattening
UTMK_LAT0_DEG: Final = 38.0
UTMK_LON0_DEG: Final = 127.5
UTMK_K0: Final = 0.9996
UTMK_FALSE_E: Final = 1_000_000.0
UTMK_FALSE_N: Final = 2_000_000.0


@dataclass(frozen=True, slots=True)
class DecodedCell:
    """One national-grid cell resolved to EPSG:5179 corner and WGS84 extent."""

    cell_id: str
    # South-west corner in EPSG:5179 metres (the value the ID literally names).
    easting_m: float
    northing_m: float
    # WGS84 centre of the square cell.
    center_lat: float
    center_lng: float
    # WGS84 axis-aligned bounding box enclosing the projected square.
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float


def _meridian_arc(lat_rad: float, e2: float) -> float:
    """Meridional arc length from the equator (Snyder 1987, eq. 3-21)."""

    return UTMK_A * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * sin(2 * lat_rad)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * sin(4 * lat_rad)
        - (35 * e2**3 / 3072) * sin(6 * lat_rad)
    )


def _utmk_to_wgs84(easting_m: float, northing_m: float) -> tuple[float, float]:
    """Invert the UTM-K Transverse Mercator to (lat, lng) in degrees.

    Standard inverse TM series with footpoint latitude, from Snyder, *Map
    Projections: A Working Manual* (USGS Professional Paper 1395, 1987),
    equations 8-17 through 8-25 and 3-24/3-26.  Seoul sits within 0.5° of the
    127.5° central meridian, so the truncated series is accurate to well under
    a millimetre.
    """

    f = 1.0 / UTMK_INV_F
    e2 = 2 * f - f * f  # first eccentricity squared
    ep2 = e2 / (1 - e2)  # second eccentricity squared

    m0 = _meridian_arc(radians(UTMK_LAT0_DEG), e2)
    m = m0 + (northing_m - UTMK_FALSE_N) / UTMK_K0
    mu = m / (UTMK_A * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))

    e1 = (1 - sqrt(1 - e2)) / (1 + sqrt(1 - e2))
    # Footpoint latitude (Snyder eq. 3-26).
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * sin(4 * mu)
        + (151 * e1**3 / 96) * sin(6 * mu)
        + (1097 * e1**4 / 512) * sin(8 * mu)
    )

    sin_phi1 = sin(phi1)
    cos_phi1 = cos(phi1)
    tan_phi1 = tan(phi1)
    c1 = ep2 * cos_phi1**2
    t1 = tan_phi1**2
    n1 = UTMK_A / sqrt(1 - e2 * sin_phi1**2)
    r1 = UTMK_A * (1 - e2) / (1 - e2 * sin_phi1**2) ** 1.5
    d = (easting_m - UTMK_FALSE_E) / (n1 * UTMK_K0)

    lat_rad = phi1 - (n1 * tan_phi1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2)
        * d**6
        / 720
    )
    lng_rad = radians(UTMK_LON0_DEG) + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2)
        * d**5
        / 120
    ) / cos_phi1

    return degrees(lat_rad), degrees(lng_rad)


def _corner_easting_northing(cell_id: str) -> tuple[float, float]:
    """Parse a national-grid ``CELL_ID`` into its EPSG:5179 SW corner.

    Raises ``ValueError`` for anything that is not a 250m national-grid cell:
    wrong length, letters outside the assigned tables, non-numeric digits, or
    digits that do not land on the 250m lattice.
    """

    if not isinstance(cell_id, str):
        raise ValueError("cell_id must be a string")
    text = cell_id.strip()
    if len(text) != 10:
        raise ValueError(f"cell_id must be 2 letters + 8 digits: {cell_id!r}")

    letter_e, letter_n, digits = text[0], text[1], text[2:]
    if letter_e not in EASTING_LETTERS:
        raise ValueError(f"unknown easting letter {letter_e!r} in {cell_id!r}")
    if letter_n not in NORTHING_LETTERS:
        raise ValueError(f"unknown northing letter {letter_n!r} in {cell_id!r}")
    if not digits.isascii() or not digits.isdigit():
        raise ValueError(f"cell_id digits must be 8 ASCII digits: {cell_id!r}")

    east_units = int(digits[:4])
    north_units = int(digits[4:])
    easting_m = (
        NATIONAL_GRID_ORIGIN_E
        + EASTING_LETTERS.index(letter_e) * HUNDRED_KM_M
        + east_units * DIGIT_UNIT_M
    )
    northing_m = (
        NATIONAL_GRID_ORIGIN_N
        + NORTHING_LETTERS.index(letter_n) * HUNDRED_KM_M
        + north_units * DIGIT_UNIT_M
    )
    # A 250m cell's SW corner is a multiple of 250 m; reject off-lattice ids.
    if easting_m % CELL_SIZE_M or northing_m % CELL_SIZE_M:
        raise ValueError(f"cell_id is not aligned to the 250m grid: {cell_id!r}")
    return easting_m, northing_m


def decode_cell_id(cell_id: str) -> DecodedCell:
    """Decode a national-grid ``CELL_ID`` into WGS84 bounds and centre.

    Pure and deterministic: the same string always yields the same cell.
    """

    easting_m, northing_m = _corner_easting_northing(cell_id)
    center_lat, center_lng = _utmk_to_wgs84(
        easting_m + CELL_SIZE_M / 2, northing_m + CELL_SIZE_M / 2
    )
    # The cell is axis-aligned in the projection, not in lat/lng, so take the
    # true enclosing bbox from all four projected corners.
    corners = cell_wgs84_corners(cell_id)
    lats = [lat for lat, _ in corners]
    lngs = [lng for _, lng in corners]
    return DecodedCell(
        cell_id=cell_id.strip(),
        easting_m=easting_m,
        northing_m=northing_m,
        center_lat=center_lat,
        center_lng=center_lng,
        min_lat=min(lats),
        min_lng=min(lngs),
        max_lat=max(lats),
        max_lng=max(lngs),
    )


def cell_wgs84_corners(cell_id: str) -> tuple[tuple[float, float], ...]:
    """Return the projected cell corners as ``(lat, lng)`` in ring order.

    The order is south-west, south-east, north-east, north-west.  Keeping the
    exact transformed quadrilateral separate from :class:`DecodedCell`'s
    axis-aligned display bbox prevents spatial joins from counting the bbox's
    small corner excess as part of the cell.
    """

    easting_m, northing_m = _corner_easting_northing(cell_id)
    return tuple(
        _utmk_to_wgs84(easting_m + de, northing_m + dn)
        for de, dn in (
            (0.0, 0.0),
            (CELL_SIZE_M, 0.0),
            (CELL_SIZE_M, CELL_SIZE_M),
            (0.0, CELL_SIZE_M),
        )
    )
