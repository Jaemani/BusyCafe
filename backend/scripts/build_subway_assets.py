"""Build deterministic Seoul subway overlay assets from explicit raw caches.

Default execution is offline and read-only. ``--fetch`` is the only network
path; ``--apply`` is the only path that publishes frontend GeoJSON files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import httpx

from app.config import (
    BACKEND_DIR,
    HTTP_USER_AGENT,
    SEOUL_API_BASE_URL,
    SEOUL_BBOX,
    SUBWAY_COORDINATE_DECIMALS,
    SUBWAY_EXIT_MATCH_MAX_M,
    SUBWAY_STATION_CLUSTER_MAX_M,
    get_settings,
)


REPO_DIR = BACKEND_DIR.parent
CACHE_DIR = BACKEND_DIR / "data" / "subway"
DEFAULT_STATIONS_CACHE = CACHE_DIR / "seoul-stations.json"
DEFAULT_NETWORK_CACHE = CACHE_DIR / "osm-subway-network.json"
DEFAULT_EXITS_CACHE = CACHE_DIR / "osm-subway-exits.json"
DEFAULT_LINES_OUTPUT = REPO_DIR / "frontend/public/data/seoul-subway-lines.geojson"
DEFAULT_STATIONS_OUTPUT = REPO_DIR / "frontend/public/data/seoul-subway-stations.geojson"
DEFAULT_EXITS_OUTPUT = REPO_DIR / "frontend/public/data/seoul-subway-exits.geojson"

OVERPASS_URL = "https://overpass.openstreetmap.fr/api/interpreter"
STATION_SERVICE = "subwayStationMaster"
SCHEMA_VERSION = 1

LINE_SPECS: dict[str, tuple[str, str, int]] = {
    "1": ("1호선", "#004A85", 10),
    "2": ("2호선", "#00A23F", 20),
    "3": ("3호선", "#ED6C00", 30),
    "4": ("4호선", "#009BCE", 40),
    "5": ("5호선", "#794698", 50),
    "6": ("6호선", "#7C4932", 60),
    "7": ("7호선", "#6E7E31", 70),
    "8": ("8호선", "#D11D70", 80),
    "9": ("9호선", "#A49D87", 90),
    "신분당": ("신분당선", "#B81B30", 100),
    "공항철도": ("공항철도", "#0079AC", 110),
    "경의중앙": ("경의·중앙선", "#6AC2B3", 120),
    "경춘": ("경춘선", "#007A62", 130),
    "수인분당": ("수인·분당선", "#ECA300", 140),
    "우이신설": ("우이신설선", "#BACC50", 150),
    "신림": ("신림선", "#6789CA", 160),
    "서해": ("서해선", "#5EAC41", 170),
    "김포골드": ("김포 골드라인", "#957326", 180),
    "GTX-A": ("GTX-A", "#AB087D", 190),
}
LINE_ALIASES = {
    "1호선": "1",
    "2호선": "2",
    "3호선": "3",
    "4호선": "4",
    "5호선": "5",
    "6호선": "6",
    "7호선": "7",
    "8호선": "8",
    "9호선": "9",
    "신분당선": "신분당",
    "신분당선(연장)": "신분당",
    "신분당선(연장2)": "신분당",
    "신분당": "신분당",
    "경부선": "1",
    "경원선": "1",
    "경인선": "1",
    "일산선": "3",
    "과천선": "4",
    "진접선": "4",
    "7호선(인천)": "7",
    "별내선": "8",
    "9호선(연장)": "9",
    "공항철도1호선": "공항철도",
    "경의중앙선": "경의중앙",
    "중앙선": "경의중앙",
    "경춘선": "경춘",
    "분당선": "수인분당",
    "수인선": "수인분당",
    "우이신설선": "우이신설",
    "신림선": "신림",
    "서해선": "서해",
    "김포골드라인": "김포골드",
    "수도권 광역급행철도": "GTX-A",
}
OSM_LINE_ALIASES = {
    **{line_id: line_id for line_id in LINE_SPECS},
    "경의·중앙": "경의중앙",
    "수인·분당": "수인분당",
    "W": "우이신설",
    "Silim": "신림",
    "김포 골드라인": "김포골드",
}
EXIT_NUMBER = re.compile(r"^(\d{1,2}(?:-\d{1,2})?)$")
EXIT_NUMBER_IN_TEXT = re.compile(r"(\d{1,2}(?:-\d{1,2})?)\s*번?\s*(?:출구|출입구)")


class SubwayAssetError(ValueError):
    pass


def _payload(raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, dict):
        raise SubwayAssetError("source root must be an object")
    if "payload" in raw:
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise SubwayAssetError("source payload must be an object")
        return payload, {
            "fetched_at": raw.get("fetched_at"),
            "source": raw.get("source"),
        }
    return raw, {}


def _coordinate(lng: Any, lat: Any) -> tuple[float, float] | None:
    try:
        x, y = float(lng), float(lat)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
    if not (min_lng <= x <= max_lng and min_lat <= y <= max_lat):
        return None
    return (
        round(x, SUBWAY_COORDINATE_DECIMALS),
        round(y, SUBWAY_COORDINATE_DECIMALS),
    )


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lng1, lat1 = map(math.radians, a)
    lng2, lat2 = map(math.radians, b)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    value = math.sin(dlat / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(min(1.0, value)))


def _normalized_station_name(value: str) -> str:
    normalized = "".join(value.split()).lower()
    return normalized[:-1] if normalized.endswith("역") else normalized


def _line_sort(line_id: str) -> tuple[int, str]:
    return (LINE_SPECS[line_id][2], line_id)


def build_stations(raw: Any) -> dict[str, Any]:
    payload, cache_meta = _payload(raw)
    service = payload.get(STATION_SERVICE)
    if not isinstance(service, dict) or not isinstance(service.get("row"), list):
        raise SubwayAssetError("station cache lacks subwayStationMaster.row")
    result = service.get("RESULT")
    if not isinstance(result, dict) or result.get("CODE") != "INFO-000":
        raise SubwayAssetError("station cache does not contain a successful result")
    rows: list[dict[str, Any]] = []
    unmapped_routes: Counter[str] = Counter()
    for item in service["row"]:
        if not isinstance(item, dict):
            continue
        station_id = str(item.get("BLDN_ID", "")).strip()
        name = str(item.get("BLDN_NM", "")).strip()
        source_route = str(item.get("ROUTE", "")).strip()
        line_id = LINE_ALIASES.get(source_route)
        coordinate = _coordinate(item.get("LOT"), item.get("LAT"))
        if line_id is None:
            unmapped_routes[source_route or "(empty)"] += 1
            continue
        if not station_id or not name or coordinate is None:
            continue
        rows.append(
            {
                "station_id": station_id,
                "name": name,
                "line_id": line_id,
                "coordinate": coordinate,
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_normalized_station_name(row["name"])].append(row)

    features: list[dict[str, Any]] = []
    for name_key in sorted(grouped):
        candidates = sorted(
            grouped[name_key], key=lambda row: (row["station_id"], row["line_id"])
        )
        parents = list(range(len(candidates)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[max(left_root, right_root)] = min(left_root, right_root)

        for left in range(len(candidates)):
            for right in range(left + 1, len(candidates)):
                if _haversine_m(
                    candidates[left]["coordinate"], candidates[right]["coordinate"]
                ) <= SUBWAY_STATION_CLUSTER_MAX_M:
                    union(left, right)

        components: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for index, candidate in enumerate(candidates):
            components[find(index)].append(candidate)
        for component in components.values():
            station_id = min(row["station_id"] for row in component)
            names = Counter(row["name"] for row in component)
            name = sorted(names, key=lambda value: (-names[value], len(value), value))[0]
            line_ids = sorted({row["line_id"] for row in component}, key=_line_sort)
            lng = round(
                sum(row["coordinate"][0] for row in component) / len(component),
                SUBWAY_COORDINATE_DECIMALS,
            )
            lat = round(
                sum(row["coordinate"][1] for row in component) / len(component),
                SUBWAY_COORDINATE_DECIMALS,
            )
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lng, lat]},
                    "properties": {
                        "station_id": station_id,
                        "name": name,
                        "line_ids": line_ids,
                        "color": LINE_SPECS[line_ids[0]][1],
                        "label_priority": 10 - min(9, len(line_ids)),
                    },
                }
            )
    features.sort(key=lambda feature: feature["properties"]["station_id"])
    return {
        "type": "FeatureCollection",
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "source_id": "seoul-oa-21232-subwayStationMaster",
            "source_url": "https://data.seoul.go.kr/dataList/OA-21232/A/1/datasetView.do",
            "license": "서울 열린데이터광장 이용약관",
            "source_count": len(service["row"]),
            "accepted_source_rows": len(rows),
            "unmapped_route_counts": dict(sorted(unmapped_routes.items())),
            **cache_meta,
        },
        "features": features,
    }


def _inside_runs(geometry: Any) -> list[list[list[float]]]:
    if not isinstance(geometry, list):
        return []
    runs: list[list[list[float]]] = []
    current: list[list[float]] = []
    for point in geometry:
        coordinate = None
        if isinstance(point, dict):
            coordinate = _coordinate(point.get("lon"), point.get("lat"))
        if coordinate is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        as_list = [coordinate[0], coordinate[1]]
        if not current or current[-1] != as_list:
            current.append(as_list)
    if len(current) >= 2:
        runs.append(current)
    return runs


def _canonical_segment(points: list[list[float]]) -> tuple[tuple[float, float], ...]:
    forward = tuple((point[0], point[1]) for point in points)
    reverse = tuple(reversed(forward))
    return min(forward, reverse)


def build_lines(raw: Any) -> dict[str, Any]:
    payload, cache_meta = _payload(raw)
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise SubwayAssetError("OSM network cache lacks elements")
    ways = {
        element.get("id"): element
        for element in elements
        if isinstance(element, dict) and element.get("type") == "way"
    }
    segments: dict[str, set[tuple[tuple[float, float], ...]]] = defaultdict(set)
    for relation in elements:
        if not isinstance(relation, dict) or relation.get("type") != "relation":
            continue
        tags = relation.get("tags")
        members = relation.get("members")
        if not isinstance(tags, dict) or not isinstance(members, list):
            continue
        line_id = OSM_LINE_ALIASES.get(str(tags.get("ref", "")).strip())
        has_metro_network = tags.get("network:wikidata") == "Q16950"
        is_verified_exception = str(tags.get("ref", "")).strip() in {
            "W",
            "Silim",
            "김포 골드라인",
        }
        if (
            line_id not in LINE_SPECS
            or tags.get("route") not in {"subway", "train", "light_rail"}
            or not (has_metro_network or is_verified_exception)
        ):
            continue
        for member in members:
            if not isinstance(member, dict) or member.get("type") != "way":
                continue
            way = ways.get(member.get("ref"))
            if not isinstance(way, dict):
                continue
            for run in _inside_runs(way.get("geometry")):
                segments[line_id].add(_canonical_segment(run))

    features: list[dict[str, Any]] = []
    for line_id in sorted(segments, key=_line_sort):
        coordinates = [
            [[lng, lat] for lng, lat in segment]
            for segment in sorted(segments[line_id])
        ]
        if not coordinates:
            continue
        line_name, color, priority = LINE_SPECS[line_id]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "MultiLineString", "coordinates": coordinates},
                "properties": {
                    "line_id": line_id,
                    "line_name": line_name,
                    "color": color,
                    "label_priority": priority,
                },
            }
        )
    osm_meta = payload.get("osm3s") if isinstance(payload.get("osm3s"), dict) else {}
    missing_line_ids = sorted(set(LINE_SPECS) - set(segments), key=_line_sort)
    return {
        "type": "FeatureCollection",
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "source_id": "openstreetmap-overpass-seoul-subway-routes",
            "source_url": "https://www.openstreetmap.org",
            "license": "ODbL 1.0",
            "source_snapshot": osm_meta.get("timestamp_osm_base"),
            "missing_line_ids": missing_line_ids,
            **cache_meta,
        },
        "features": features,
    }


def _exit_number(tags: dict[str, Any]) -> str | None:
    ref = str(tags.get("ref", "")).strip()
    if EXIT_NUMBER.fullmatch(ref):
        return ref
    for key in ("description:ko", "description", "name:ko", "name"):
        match = EXIT_NUMBER_IN_TEXT.search(str(tags.get(key, "")))
        if match:
            return match.group(1)
    return None


def build_exits(raw: Any, stations: dict[str, Any]) -> dict[str, Any]:
    payload, cache_meta = _payload(raw)
    elements = payload.get("elements")
    station_features = stations.get("features")
    if not isinstance(elements, list) or not isinstance(station_features, list):
        raise SubwayAssetError("exit cache or station asset is malformed")
    station_points = [
        (
            feature["properties"]["station_id"],
            feature["properties"]["name"],
            tuple(feature["geometry"]["coordinates"]),
        )
        for feature in station_features
    ]
    deduped: dict[tuple[str, str, float, float], tuple[int, dict[str, Any]]] = {}
    skipped_without_number = 0
    linked_count = 0
    unlinked_count = 0
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "node":
            continue
        tags = element.get("tags")
        coordinate = _coordinate(element.get("lon"), element.get("lat"))
        if not isinstance(tags, dict) or tags.get("railway") != "subway_entrance":
            continue
        exit_no = _exit_number(tags)
        if coordinate is None:
            continue
        if exit_no is None:
            skipped_without_number += 1
            continue
        labels = " ".join(
            str(tags.get(key, ""))
            for key in ("description:ko", "description", "name:ko", "name")
        )
        normalized_label = _normalized_station_name(labels)
        named_candidates = [
            station
            for station in station_points
            if _normalized_station_name(station[1]) in normalized_label
        ]
        osm_id = int(element.get("id", 0))
        station_id = f"osm:{osm_id}"
        station_name: str | None = None
        association = "unlinked"
        if named_candidates:
            nearest = min(
                named_candidates,
                key=lambda station: (_haversine_m(coordinate, station[2]), station[0]),
            )
            distance = _haversine_m(coordinate, nearest[2])
            if distance <= SUBWAY_EXIT_MATCH_MAX_M:
                station_id, station_name, _ = nearest
                association = "official_station"
                linked_count += 1
            else:
                unlinked_count += 1
        else:
            unlinked_count += 1
        key = (station_id, exit_no, coordinate[0], coordinate[1])
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [coordinate[0], coordinate[1]],
            },
            "properties": {
                "station_id": station_id,
                "station_name": station_name,
                "exit_no": exit_no,
                "label_priority": 1,
                "association": association,
            },
        }
        if key not in deduped or osm_id < deduped[key][0]:
            deduped[key] = (osm_id, feature)
    features = [value[1] for value in deduped.values()]
    features.sort(
        key=lambda feature: (
            feature["properties"]["station_id"],
            feature["properties"]["exit_no"],
            feature["geometry"]["coordinates"],
        )
    )
    osm_meta = payload.get("osm3s") if isinstance(payload.get("osm3s"), dict) else {}
    return {
        "type": "FeatureCollection",
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "source_id": "openstreetmap-overpass-subway-entrances",
            "source_url": "https://www.openstreetmap.org",
            "license": "ODbL 1.0",
            "source_snapshot": osm_meta.get("timestamp_osm_base"),
            "station_identity_source": "seoul-oa-21232-subwayStationMaster",
            "source_count": len(elements),
            "output_count": len(features),
            "linked_count": linked_count,
            "unlinked_count": unlinked_count,
            "skipped_without_exit_number": skipped_without_number,
            **cache_meta,
        },
        "features": features,
    }


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, value: Any, *, replace: bool = True) -> None:
    if path.exists() and not replace:
        raise SubwayAssetError(f"refusing to overwrite cache: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    part.write_bytes(_json_bytes(value))
    part.replace(path)


def _fetch_json(client: httpx.Client, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = client.request("GET" if not kwargs else "POST", url, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise SubwayAssetError("external subway source fetch failed") from exc
    if not isinstance(payload, dict):
        raise SubwayAssetError("external subway source returned non-object JSON")
    return payload


def fetch_caches(*, replace: bool) -> None:
    cache_paths = (
        DEFAULT_STATIONS_CACHE,
        DEFAULT_NETWORK_CACHE,
        DEFAULT_EXITS_CACHE,
    )
    if not replace and any(path.exists() for path in cache_paths):
        raise SubwayAssetError(
            "subway cache already exists; pass --replace-cache to refresh all sources"
        )
    secret = get_settings().seoul_api_key
    if secret is None or not secret.get_secret_value():
        raise SubwayAssetError("SEOUL_API_KEY is required for --fetch")
    key = secret.get_secret_value()
    min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
    network_query = (
        "[out:json][timeout:90];"
        f'way["railway"~"^(subway|rail|light_rail)$"]({min_lat},{min_lng},{max_lat},{max_lng})->.tracks;'
        'rel(bw.tracks)["type"="route"]["route"~"^(subway|train|light_rail)$"]->.routes;'
        ".routes out body;.tracks out geom;"
    )
    exits_query = (
        "[out:json][timeout:60];"
        f'node["railway"="subway_entrance"]({min_lat},{min_lng},{max_lat},{max_lng});out body;'
    )
    fetched_at = datetime.now(UTC).isoformat()
    with httpx.Client(
        timeout=httpx.Timeout(120.0, connect=10.0),
        headers={"User-Agent": HTTP_USER_AGENT},
    ) as client:
        station_url = (
            f"{SEOUL_API_BASE_URL}/{key}/json/{STATION_SERVICE}/1/1000/"
        )
        stations = _fetch_json(client, station_url)
        network = _fetch_json(client, OVERPASS_URL, data={"data": network_query})
        exits = _fetch_json(client, OVERPASS_URL, data={"data": exits_query})
    _atomic_write(
        DEFAULT_STATIONS_CACHE,
        {
            "fetched_at": fetched_at,
            "source": "Seoul Open Data OA-21232",
            "payload": stations,
        },
        replace=replace,
    )
    _atomic_write(
        DEFAULT_NETWORK_CACHE,
        {
            "fetched_at": fetched_at,
            "source": {"endpoint": OVERPASS_URL, "query": network_query},
            "payload": network,
        },
        replace=replace,
    )
    _atomic_write(
        DEFAULT_EXITS_CACHE,
        {
            "fetched_at": fetched_at,
            "source": {"endpoint": OVERPASS_URL, "query": exits_query},
            "payload": exits,
        },
        replace=replace,
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubwayAssetError(f"cannot read subway cache: {path}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--replace-cache", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stations-cache", type=Path, default=DEFAULT_STATIONS_CACHE)
    parser.add_argument("--network-cache", type=Path, default=DEFAULT_NETWORK_CACHE)
    parser.add_argument("--exits-cache", type=Path, default=DEFAULT_EXITS_CACHE)
    args = parser.parse_args()
    if args.replace_cache and not args.fetch:
        parser.error("--replace-cache requires --fetch")
    if args.fetch:
        fetch_caches(replace=args.replace_cache)
    stations = build_stations(_read_json(args.stations_cache))
    lines = build_lines(_read_json(args.network_cache))
    if lines["metadata"]["missing_line_ids"]:
        raise SubwayAssetError(
            "OSM network cache lacks required lines: "
            + ", ".join(lines["metadata"]["missing_line_ids"])
        )
    exits = build_exits(_read_json(args.exits_cache), stations)
    assets = (
        (DEFAULT_LINES_OUTPUT, lines),
        (DEFAULT_STATIONS_OUTPUT, stations),
        (DEFAULT_EXITS_OUTPUT, exits),
    )
    for path, asset in assets:
        digest = hashlib.sha256(_json_bytes(asset)).hexdigest()
        print(f"{path.name}: {len(asset['features'])} features sha256={digest}")
        if args.apply:
            _atomic_write(path, asset)
    if not args.apply:
        print("dry-run: pass --apply to publish frontend assets")


if __name__ == "__main__":
    main()
