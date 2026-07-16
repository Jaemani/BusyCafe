import { describe, expect, it, vi } from "vitest";
import type {
  GeoJSONSourceSpecification,
  LayerSpecification,
  SymbolLayerSpecification,
} from "maplibre-gl";

import {
  addSeoulSubwayOverlay,
  SUBWAY_ASSET_PATHS,
  SUBWAY_LAYER_IDS,
} from "./subway-overlay";

class FakeMap {
  readonly sources = new Map<string, GeoJSONSourceSpecification>();
  readonly layers: Array<{ layer: LayerSpecification; beforeId?: string }> = [];

  addSource(id: string, source: GeoJSONSourceSpecification): void {
    this.sources.set(id, source);
  }

  addLayer(layer: LayerSpecification, beforeId?: string): void {
    this.layers.push({ layer, beforeId });
  }

  getSource(id: string): GeoJSONSourceSpecification | undefined {
    return this.sources.get(id);
  }

  getLayer(id: string): LayerSpecification | undefined {
    return this.layers.find(({ layer }) => layer.id === id)?.layer;
  }
}

function collection(geometry: object, properties: object = {}): object {
  return {
    type: "FeatureCollection",
    features: [{ type: "Feature", geometry, properties }],
  };
}

function jsonResponse(payload: object, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("Seoul subway overlay", () => {
  it("loads optional assets and installs collision-aware zoom layers below cafes", async () => {
    const payloads = new Map<string, object>([
      [SUBWAY_ASSET_PATHS.lines, collection(
        { type: "LineString", coordinates: [[126.9, 37.5], [127, 37.55]] },
        { line_id: "2", color: "#00a84d" },
      )],
      [SUBWAY_ASSET_PATHS.stations, collection(
        { type: "Point", coordinates: [126.98, 37.55] },
        { station_name: "시청", color: "#00a84d", label_priority: 1 },
      )],
      [SUBWAY_ASSET_PATHS.exits, collection(
        { type: "Point", coordinates: [126.981, 37.551] },
        { exit_no: "3번 출구" },
      )],
    ]);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const payload = payloads.get(String(input));
      return payload ? jsonResponse(payload) : jsonResponse({}, 404);
    });
    const fetchImpl = fetchMock as unknown as typeof fetch;
    const map = new FakeMap();
    map.addLayer({ id: "cafe-clusters", type: "background" });

    const result = await addSeoulSubwayOverlay(map, {
      beforeLayerId: "cafe-clusters",
      fetchImpl,
    });

    expect(result).toEqual({
      loaded: ["lines", "stations", "exits"],
      unavailable: [],
    });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      SUBWAY_ASSET_PATHS.lines,
      SUBWAY_ASSET_PATHS.stations,
      SUBWAY_ASSET_PATHS.exits,
    ]);
    expect(map.sources.size).toBe(3);
    const subwayLayers = map.layers.filter(
      ({ layer }) => layer.id !== "cafe-clusters",
    );
    expect(subwayLayers.map(({ layer }) => layer.id)).toEqual([
      SUBWAY_LAYER_IDS.lineCasing,
      SUBWAY_LAYER_IDS.lines,
      SUBWAY_LAYER_IDS.stationPoints,
      SUBWAY_LAYER_IDS.stationLabels,
      SUBWAY_LAYER_IDS.exitPoints,
      SUBWAY_LAYER_IDS.exitLabels,
    ]);
    expect(subwayLayers.every(({ beforeId }) => beforeId === "cafe-clusters"))
      .toBe(true);

    const stationLabels = map.getLayer(
      SUBWAY_LAYER_IDS.stationLabels,
    ) as SymbolLayerSpecification;
    const exitLabels = map.getLayer(
      SUBWAY_LAYER_IDS.exitLabels,
    ) as SymbolLayerSpecification;
    expect(stationLabels.minzoom).toBe(13);
    expect(stationLabels.layout?.["text-allow-overlap"]).toBe(false);
    expect(stationLabels.layout?.["text-ignore-placement"]).toBe(false);
    expect(exitLabels.minzoom).toBe(16.5);
    expect(exitLabels.layout?.["text-allow-overlap"]).toBe(false);
  });

  it("ignores missing, malformed, and wrong-geometry assets without throwing", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === SUBWAY_ASSET_PATHS.lines) {
        return jsonResponse({}, 404);
      }
      if (String(input) === SUBWAY_ASSET_PATHS.stations) {
        return jsonResponse(collection({
          type: "LineString",
          coordinates: [[126.9, 37.5], [127, 37.55]],
        }));
      }
      throw new TypeError("optional asset unavailable");
    }) as unknown as typeof fetch;
    const map = new FakeMap();

    await expect(addSeoulSubwayOverlay(map, { fetchImpl })).resolves.toEqual({
      loaded: [],
      unavailable: ["lines", "stations", "exits"],
    });
    expect(map.sources.size).toBe(0);
    expect(map.layers).toEqual([]);
  });

  it("is idempotent when map initialization is retried", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === SUBWAY_ASSET_PATHS.lines) {
        return jsonResponse(collection({
          type: "LineString",
          coordinates: [[126.9, 37.5], [127, 37.55]],
        }));
      }
      return jsonResponse({}, 404);
    }) as unknown as typeof fetch;
    const map = new FakeMap();

    await addSeoulSubwayOverlay(map, { fetchImpl });
    await addSeoulSubwayOverlay(map, { fetchImpl });

    expect(map.sources.size).toBe(1);
    expect(map.layers.map(({ layer }) => layer.id)).toEqual([
      SUBWAY_LAYER_IDS.lineCasing,
      SUBWAY_LAYER_IDS.lines,
    ]);
  });
});
