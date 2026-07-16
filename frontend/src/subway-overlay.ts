import type {
  GeoJSONSourceSpecification,
  LayerSpecification,
} from "maplibre-gl";
import type {
  Feature,
  FeatureCollection,
  GeoJsonProperties,
  Geometry,
} from "geojson";

export const SUBWAY_ASSET_PATHS = {
  lines: "/data/seoul-subway-lines.geojson",
  stations: "/data/seoul-subway-stations.geojson",
  exits: "/data/seoul-subway-exits.geojson",
} as const;

export const SUBWAY_LAYER_IDS = {
  lineCasing: "seoul-subway-line-casing",
  lines: "seoul-subway-lines",
  stationPoints: "seoul-subway-station-points",
  stationLabels: "seoul-subway-station-labels",
  exitPoints: "seoul-subway-exit-points",
  exitLabels: "seoul-subway-exit-labels",
} as const;

const SUBWAY_SOURCE_IDS = {
  lines: "seoul-subway-lines-source",
  stations: "seoul-subway-stations-source",
  exits: "seoul-subway-exits-source",
} as const;

type SubwayAsset = keyof typeof SUBWAY_ASSET_PATHS;

interface SubwayOverlayMap {
  addSource(id: string, source: GeoJSONSourceSpecification): void;
  addLayer(layer: LayerSpecification, beforeId?: string): void;
  getSource(id: string): unknown;
  getLayer(id: string): unknown;
}

interface SubwayOverlayOptions {
  beforeLayerId?: string;
  fetchImpl?: typeof fetch;
}

export interface SubwayOverlayResult {
  loaded: SubwayAsset[];
  unavailable: SubwayAsset[];
}

const ALLOWED_GEOMETRIES: Record<SubwayAsset, ReadonlySet<string>> = {
  lines: new Set(["LineString", "MultiLineString"]),
  stations: new Set(["Point"]),
  exits: new Set(["Point"]),
};

function usableFeatureCollection(
  value: unknown,
  asset: SubwayAsset,
): FeatureCollection | null {
  if (
    typeof value !== "object" ||
    value === null ||
    (value as { type?: unknown }).type !== "FeatureCollection" ||
    !Array.isArray((value as { features?: unknown }).features)
  ) {
    return null;
  }

  const features = (value as FeatureCollection).features.filter(
    (feature): feature is Feature<Geometry, GeoJsonProperties> =>
      feature?.type === "Feature" &&
      typeof feature.geometry === "object" &&
      feature.geometry !== null &&
      ALLOWED_GEOMETRIES[asset].has(feature.geometry.type),
  );
  return features.length > 0 ? { type: "FeatureCollection", features } : null;
}

async function loadOptionalAsset(
  asset: SubwayAsset,
  fetchImpl: typeof fetch,
): Promise<FeatureCollection | null> {
  try {
    const response = await fetchImpl(SUBWAY_ASSET_PATHS[asset], {
      cache: "force-cache",
    });
    if (!response.ok) return null;
    return usableFeatureCollection(await response.json(), asset);
  } catch {
    return null;
  }
}

function safeAddSource(
  map: SubwayOverlayMap,
  id: string,
  data: FeatureCollection,
): boolean {
  try {
    if (map.getSource(id)) return true;
    map.addSource(id, { type: "geojson", data });
    return true;
  } catch {
    return false;
  }
}

function safeAddLayer(
  map: SubwayOverlayMap,
  layer: LayerSpecification,
  beforeLayerId?: string,
): void {
  try {
    if (map.getLayer(layer.id)) return;
    const beforeId = beforeLayerId && map.getLayer(beforeLayerId)
      ? beforeLayerId
      : undefined;
    map.addLayer(layer, beforeId);
  } catch {
    // Optional context must never prevent the cafe map from rendering.
  }
}

function addLineLayers(map: SubwayOverlayMap, beforeLayerId?: string): void {
  safeAddLayer(map, {
    id: SUBWAY_LAYER_IDS.lineCasing,
    type: "line",
    source: SUBWAY_SOURCE_IDS.lines,
    minzoom: 9,
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": "rgba(255, 255, 255, 0.9)",
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 3.2, 16, 7.5],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.5, 12, 0.8],
    },
  }, beforeLayerId);
  safeAddLayer(map, {
    id: SUBWAY_LAYER_IDS.lines,
    type: "line",
    source: SUBWAY_SOURCE_IDS.lines,
    minzoom: 9,
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": ["coalesce", ["get", "color"], "#65727a"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 1.6, 16, 4.5],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.72, 12, 0.96],
    },
  }, beforeLayerId);
}

function addStationLayers(map: SubwayOverlayMap, beforeLayerId?: string): void {
  safeAddLayer(map, {
    id: SUBWAY_LAYER_IDS.stationPoints,
    type: "circle",
    source: SUBWAY_SOURCE_IDS.stations,
    minzoom: 11.5,
    paint: {
      "circle-color": "#ffffff",
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 11.5, 2.5, 16, 5],
      "circle-stroke-color": ["coalesce", ["get", "color"], "#35443e"],
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 11.5, 1.5, 16, 2.5],
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 11.5, 0.75, 14, 1],
    },
  }, beforeLayerId);
  safeAddLayer(map, {
    id: SUBWAY_LAYER_IDS.stationLabels,
    type: "symbol",
    source: SUBWAY_SOURCE_IDS.stations,
    minzoom: 13,
    layout: {
      "text-field": ["coalesce", ["get", "station_name"], ["get", "name"], ""],
      "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 13, 10, 17, 12],
      "text-variable-anchor": ["top", "bottom", "left", "right"],
      "text-radial-offset": 0.7,
      "text-padding": 3,
      "text-allow-overlap": false,
      "text-ignore-placement": false,
      "symbol-sort-key": ["coalesce", ["get", "label_priority"], 100],
    },
    paint: {
      "text-color": "#26352f",
      "text-halo-color": "rgba(255, 255, 255, 0.96)",
      "text-halo-width": 1.5,
      "text-halo-blur": 0.5,
    },
  }, beforeLayerId);
}

function addExitLayers(map: SubwayOverlayMap, beforeLayerId?: string): void {
  safeAddLayer(map, {
    id: SUBWAY_LAYER_IDS.exitPoints,
    type: "circle",
    source: SUBWAY_SOURCE_IDS.exits,
    minzoom: 15.5,
    paint: {
      "circle-color": "#ffffff",
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 15.5, 2.5, 18, 4],
      "circle-stroke-color": "#315b49",
      "circle-stroke-width": 1.5,
    },
  }, beforeLayerId);
  safeAddLayer(map, {
    id: SUBWAY_LAYER_IDS.exitLabels,
    type: "symbol",
    source: SUBWAY_SOURCE_IDS.exits,
    minzoom: 16.5,
    layout: {
      "text-field": ["coalesce", ["get", "exit_name"], ["get", "exit_no"], ["get", "name"], ""],
      "text-font": ["Noto Sans Regular"],
      "text-size": 9,
      "text-offset": [0, 0.9],
      "text-anchor": "top",
      "text-padding": 2,
      "text-allow-overlap": false,
      "text-ignore-placement": false,
      "symbol-sort-key": ["coalesce", ["get", "label_priority"], 100],
    },
    paint: {
      "text-color": "#315b49",
      "text-halo-color": "rgba(255, 255, 255, 0.96)",
      "text-halo-width": 1.25,
    },
  }, beforeLayerId);
}

export async function addSeoulSubwayOverlay(
  map: SubwayOverlayMap,
  options: SubwayOverlayOptions = {},
): Promise<SubwayOverlayResult> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const assets = Object.keys(SUBWAY_ASSET_PATHS) as SubwayAsset[];
  const loadedAssets = await Promise.all(
    assets.map(async (asset) => [asset, await loadOptionalAsset(asset, fetchImpl)] as const),
  );
  const loaded: SubwayAsset[] = [];
  const unavailable: SubwayAsset[] = [];

  for (const [asset, collection] of loadedAssets) {
    if (
      collection === null ||
      !safeAddSource(map, SUBWAY_SOURCE_IDS[asset], collection)
    ) {
      unavailable.push(asset);
      continue;
    }
    loaded.push(asset);
    if (asset === "lines") addLineLayers(map, options.beforeLayerId);
    if (asset === "stations") addStationLayers(map, options.beforeLayerId);
    if (asset === "exits") addExitLayers(map, options.beforeLayerId);
  }

  return { loaded, unavailable };
}
