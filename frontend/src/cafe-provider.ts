import type { Feature, FeatureCollection, Point } from "geojson";

export interface CafeMapProperties {
  id: string;
  name: string;
  lat: number;
  lng: number;
  coverage: "covered" | "fringe" | "uncovered";
  level: 1 | 2 | 3 | 4 | null;
  confidence: number | null;
  freshness: "fresh" | "delayed" | "stale" | "n/a";
  hotspotName: string | null;
  distanceM: number | null;
  observedAt: string | null;
  observationAgeMinutes: number | null;
  observationAgeMeasuredAtMs: number;
}

export interface CafeProperties extends CafeMapProperties {
  address: string;
  phone: string | null;
  website: string | null;
  sourceLabel: string;
  naverUrl: string | null;
  naverSearchUrl: string | null;
  kakaoUrl: string | null;
  googleUrl: string | null;
  confidenceTier: "high" | "mid" | "low" | null;
}

export type CafeFeature = Feature<Point, CafeMapProperties>;
export type CafeFeatureCollection = FeatureCollection<Point, CafeMapProperties>;

export interface CafeViewport {
  minLng: number;
  minLat: number;
  maxLng: number;
  maxLat: number;
  zoom: number;
}

/**
 * 지도는 POI 원장을 알지 못한다. 검증된 서버 API가 이 경계를 구현하며
 * 베이스맵 타일의 POI는 애플리케이션 카페 데이터로 사용하지 않는다.
 */
export interface CafeProvider {
  getCafes(viewport: CafeViewport, signal: AbortSignal): Promise<CafeFeatureCollection>;
  getCafeDetail(cafeId: string, signal: AbortSignal): Promise<CafeProperties>;
  setCacheVersion?(version: string | null): void;
}

export class EmptyCafeProvider implements CafeProvider {
  async getCafes(
    _viewport: CafeViewport,
    _signal: AbortSignal,
  ): Promise<CafeFeatureCollection> {
    return { type: "FeatureCollection", features: [] };
  }

  async getCafeDetail(_cafeId: string, _signal: AbortSignal): Promise<CafeProperties> {
    throw new Error("카페 상세 정보를 불러올 수 없습니다");
  }
}

interface CafeApiItem {
  id: string | number;
  name: string;
  lat: number;
  lng: number;
  level?: 1 | 2 | 3 | 4 | null;
  confidence?: number | null;
  freshness?: "fresh" | "delayed" | "stale" | "n/a";
  coverage?: "covered" | "fringe" | "uncovered";
  age_minutes?: number | null;
  evidence?: {
    hotspot_name?: string | null;
    distance_m?: number | null;
    observed_at?: string | null;
    age_minutes?: number | null;
  } | null;
}

interface CafeDetailApiItem extends CafeApiItem {
  road_address?: string | null;
  phone?: string | null;
  website?: string | null;
  source_label?: string | null;
  confidence_tier?: "high" | "mid" | "low" | null;
  external_links?: {
    naver?: string | null;
    naver_search?: string | null;
    kakao?: string | null;
    google?: string | null;
  } | null;
}

const VIEWPORT_TRUNCATED_HEADER = "X-BusyCafe-Viewport-Truncated";
const MAX_VIEWPORT_SPLIT_DEPTH = 6;
const MERCATOR_MAX_LAT = 85.05112878;
const MIN_QUERY_TILE_ZOOM = 10;
const MAX_QUERY_TILE_ZOOM = 15;
const MAX_ROOT_TILE_REQUESTS = 16;
const MAX_CONCURRENT_REQUESTS = 6;
const TILE_CACHE_TTL_MS = 60_000;
const MAX_CACHED_TILES = 160;

interface CafeTile {
  z: number;
  x: number;
  y: number;
}

interface CachedCafeTile {
  expiresAtMs: number;
  features: CafeFeature[];
}

interface CachedCafeDetail {
  expiresAtMs: number;
  cafe: CafeProperties;
}

interface TileRange {
  z: number;
  firstX: number;
  lastX: number;
  firstY: number;
  lastY: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function longitudeToTileX(longitude: number, tileCount: number): number {
  return ((longitude + 180) / 360) * tileCount;
}

function latitudeToTileY(latitude: number, tileCount: number): number {
  const clampedLatitude = clamp(latitude, -MERCATOR_MAX_LAT, MERCATOR_MAX_LAT);
  const latitudeRadians = clampedLatitude * Math.PI / 180;
  return (
    1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI
  ) / 2 * tileCount;
}

function tileRangeForViewport(viewport: CafeViewport, z: number): TileRange {
  const tileCount = 2 ** z;
  const west = viewport.minLng;
  let east = viewport.maxLng;
  if (east < west) east += 360;
  if (east - west >= 360) {
    return {
      z,
      firstX: 0,
      lastX: tileCount - 1,
      firstY: clamp(Math.floor(latitudeToTileY(viewport.maxLat, tileCount)), 0, tileCount - 1),
      lastY: clamp(Math.ceil(latitudeToTileY(viewport.minLat, tileCount)) - 1, 0, tileCount - 1),
    };
  }

  return {
    z,
    firstX: Math.floor(longitudeToTileX(west, tileCount)),
    lastX: Math.ceil(longitudeToTileX(east, tileCount)) - 1,
    firstY: clamp(Math.floor(latitudeToTileY(viewport.maxLat, tileCount)), 0, tileCount - 1),
    lastY: clamp(Math.ceil(latitudeToTileY(viewport.minLat, tileCount)) - 1, 0, tileCount - 1),
  };
}

function tileRangeSize(range: TileRange): number {
  return Math.max(0, range.lastX - range.firstX + 1) *
    Math.max(0, range.lastY - range.firstY + 1);
}

function tilesForViewport(viewport: CafeViewport): CafeTile[] {
  let z = clamp(
    Math.floor(viewport.zoom),
    MIN_QUERY_TILE_ZOOM,
    MAX_QUERY_TILE_ZOOM,
  );
  let range = tileRangeForViewport(viewport, z);
  while (z > MIN_QUERY_TILE_ZOOM && tileRangeSize(range) > MAX_ROOT_TILE_REQUESTS) {
    z -= 1;
    range = tileRangeForViewport(viewport, z);
  }

  const tileCount = 2 ** z;
  const uniqueTiles = new Map<string, CafeTile>();
  for (let x = range.firstX; x <= range.lastX; x += 1) {
    const canonicalX = ((x % tileCount) + tileCount) % tileCount;
    for (let y = range.firstY; y <= range.lastY; y += 1) {
      const tile = { z, x: canonicalX, y };
      uniqueTiles.set(`${z}/${canonicalX}/${y}`, tile);
    }
  }
  return [...uniqueTiles.values()];
}

function tileBounds(tile: CafeTile): CafeViewport {
  const tileCount = 2 ** tile.z;
  const longitude = (x: number): number => x / tileCount * 360 - 180;
  const latitude = (y: number): number => {
    const mercatorY = Math.PI * (1 - 2 * y / tileCount);
    return Math.atan(Math.sinh(mercatorY)) * 180 / Math.PI;
  };
  return {
    minLng: longitude(tile.x),
    minLat: latitude(tile.y + 1),
    maxLng: longitude(tile.x + 1),
    maxLat: latitude(tile.y),
    zoom: tile.z,
  };
}

function tileChildren(tile: CafeTile): CafeTile[] {
  const z = tile.z + 1;
  const x = tile.x * 2;
  const y = tile.y * 2;
  return [
    { z, x, y },
    { z, x: x + 1, y },
    { z, x, y: y + 1 },
    { z, x: x + 1, y: y + 1 },
  ];
}

function featureIsInsideViewport(feature: CafeFeature, viewport: CafeViewport): boolean {
  const [longitude, latitude] = feature.geometry.coordinates;
  const insideLongitude = viewport.maxLng >= viewport.minLng
    ? longitude >= viewport.minLng && longitude <= viewport.maxLng
    : longitude >= viewport.minLng || longitude <= viewport.maxLng;
  return insideLongitude && latitude >= viewport.minLat && latitude <= viewport.maxLat;
}

export class CachedApiCafeProvider implements CafeProvider {
  private readonly tileCache = new Map<string, CachedCafeTile>();
  private readonly detailCache = new Map<string, CachedCafeDetail>();
  private readonly splitTiles = new Set<string>();
  private activeRequestCount = 0;
  private readonly requestWaiters: Array<() => void> = [];
  private cacheVersion: string | null = null;

  constructor(private readonly apiBaseUrl = "") {}

  setCacheVersion(version: string | null): void {
    this.cacheVersion = version;
  }

  async getCafes(
    viewport: CafeViewport,
    signal: AbortSignal,
  ): Promise<CafeFeatureCollection> {
    signal.throwIfAborted();
    const featureGroups = await Promise.all(
      tilesForViewport(viewport).map((tile) => this.getTileFeatures(tile, signal, 0)),
    );
    const deduplicated = new Map<string, CafeFeature>();
    for (const feature of featureGroups.flat().filter(
      (candidate) => featureIsInsideViewport(candidate, viewport),
    )) {
      deduplicated.set(feature.properties.id, feature);
    }
    return { type: "FeatureCollection", features: [...deduplicated.values()] };
  }

  async getCafeDetail(
    cafeId: string,
    signal: AbortSignal,
  ): Promise<CafeProperties> {
    signal.throwIfAborted();
    const cacheKey = `${this.cacheVersion ?? "unknown"}:${cafeId}`;
    const cached = this.detailCache.get(cacheKey);
    if (cached && cached.expiresAtMs > Date.now()) {
      this.detailCache.delete(cacheKey);
      this.detailCache.set(cacheKey, cached);
      return cached.cafe;
    }
    this.detailCache.delete(cacheKey);

    const url = new URL(
      `/api/cafes/${encodeURIComponent(cafeId)}`,
      this.apiBaseUrl || window.location.origin,
    );
    if (this.cacheVersion !== null) {
      url.searchParams.set("data_version", this.cacheVersion);
    }
    const response = await fetch(url, { signal, cache: "default" });
    if (!response.ok) throw new Error("카페 상세 정보를 불러오지 못했습니다");
    const item = (await response.json()) as CafeDetailApiItem;
    if (
      (typeof item.id !== "string" && typeof item.id !== "number") ||
      String(item.id) !== cafeId ||
      typeof item.name !== "string" ||
      !Number.isFinite(item.lat) ||
      !Number.isFinite(item.lng)
    ) {
      throw new Error("카페 상세 응답 형식이 올바르지 않습니다");
    }
    const observationAgeMeasuredAtMs = Date.now();
    const cafe: CafeProperties = {
      id: cafeId,
      name: item.name,
      address: item.road_address ?? "주소 정보 없음",
      phone: item.phone ?? null,
      website: item.website ?? null,
      lat: item.lat,
      lng: item.lng,
      sourceLabel: item.source_label ?? "서버 검증 카페 원장",
      naverUrl: item.external_links?.naver ?? null,
      naverSearchUrl: item.external_links?.naver_search ?? null,
      kakaoUrl: item.external_links?.kakao ?? null,
      googleUrl: item.external_links?.google ?? null,
      coverage: item.coverage ?? "uncovered",
      level: item.level ?? null,
      confidence: item.confidence ?? null,
      confidenceTier: item.confidence_tier ?? null,
      freshness: item.freshness ?? "n/a",
      hotspotName: item.evidence?.hotspot_name ?? null,
      distanceM: item.evidence?.distance_m ?? null,
      observedAt: item.evidence?.observed_at ?? null,
      observationAgeMinutes: this.validObservationAge(item.evidence?.age_minutes),
      observationAgeMeasuredAtMs,
    };
    this.detailCache.set(cacheKey, {
      expiresAtMs: Date.now() + TILE_CACHE_TTL_MS,
      cafe,
    });
    while (this.detailCache.size > 100) {
      const oldestKey = this.detailCache.keys().next().value as string | undefined;
      if (oldestKey === undefined) break;
      this.detailCache.delete(oldestKey);
    }
    return cafe;
  }

  private async getTileFeatures(
    tile: CafeTile,
    signal: AbortSignal,
    depth: number,
  ): Promise<CafeFeature[]> {
    signal.throwIfAborted();
    const tileKey = `${tile.z}/${tile.x}/${tile.y}`;
    const cacheKey = `${this.cacheVersion ?? "unknown"}:${tileKey}`;
    const cached = this.readCachedTile(cacheKey);
    if (cached !== null) return cached;

    if (this.splitTiles.has(tileKey)) {
      const childGroups = await Promise.all(
        tileChildren(tile).map((child) => this.getTileFeatures(child, signal, depth + 1)),
      );
      return childGroups.flat();
    }

    const viewport = tileBounds(tile);
    const bbox = [viewport.minLng, viewport.minLat, viewport.maxLng, viewport.maxLat].join(",");
    const url = new URL("/api/cafes/summary", this.apiBaseUrl || window.location.origin);
    url.searchParams.set("bbox", bbox);
    url.searchParams.set("min_conf", "0");
    if (this.cacheVersion !== null) {
      url.searchParams.set("data_version", this.cacheVersion);
    }

    const { items, truncated } = await this.withRequestSlot(signal, async () => {
      const response = await fetch(url, { signal, cache: "default" });
      if (!response.ok) {
        throw new Error("검증된 카페 데이터 서버에 연결할 수 없습니다");
      }
      const responseItems = (await response.json()) as CafeApiItem[];
      if (!Array.isArray(responseItems)) {
        throw new Error("카페 데이터 응답 형식이 올바르지 않습니다");
      }
      const truncatedHeader = response.headers.get(VIEWPORT_TRUNCATED_HEADER);
      if (truncatedHeader !== "true" && truncatedHeader !== "false") {
        throw new Error("카페 데이터 완전성 상태를 확인할 수 없습니다");
      }
      return { items: responseItems, truncated: truncatedHeader === "true" };
    });

    if (truncated) {
      if (depth >= MAX_VIEWPORT_SPLIT_DEPTH) {
        throw new Error("표시 영역에 카페가 너무 많아 전체 목록을 불러오지 못했습니다");
      }
      signal.throwIfAborted();
      this.splitTiles.add(tileKey);
      const nested = await Promise.all(
        tileChildren(tile).map((child) => this.getTileFeatures(child, signal, depth + 1)),
      );
      return nested.flat();
    }

    const observationAgeMeasuredAtMs = Date.now();

    const features = items.flatMap((item): CafeFeature[] => {
      if (
        (typeof item.id !== "string" && typeof item.id !== "number") ||
        typeof item.name !== "string" ||
        !Number.isFinite(item.lat) ||
        !Number.isFinite(item.lng)
      ) {
        return [];
      }
      const id = String(item.id);
      return [{
        type: "Feature",
        id,
        geometry: { type: "Point", coordinates: [item.lng, item.lat] },
        properties: {
          id,
          name: item.name,
          lat: item.lat,
          lng: item.lng,
          coverage: item.coverage ?? "uncovered",
          level: item.level ?? null,
          confidence: item.confidence ?? null,
          freshness: item.freshness ?? "n/a",
          hotspotName: item.evidence?.hotspot_name ?? null,
          distanceM: item.evidence?.distance_m ?? null,
          observedAt: item.evidence?.observed_at ?? null,
          observationAgeMinutes: this.validObservationAge(
            item.age_minutes ?? item.evidence?.age_minutes,
          ),
          observationAgeMeasuredAtMs,
        },
      }];
    });

    this.cacheTile(cacheKey, features);
    return features;
  }

  private validObservationAge(value: number | null | undefined): number | null {
    return typeof value === "number" && Number.isFinite(value) && value >= 0
      ? value
      : null;
  }

  private readCachedTile(cacheKey: string): CafeFeature[] | null {
    const cached = this.tileCache.get(cacheKey);
    if (!cached) return null;
    if (cached.expiresAtMs <= Date.now()) {
      this.tileCache.delete(cacheKey);
      return null;
    }
    this.tileCache.delete(cacheKey);
    this.tileCache.set(cacheKey, cached);
    return cached.features;
  }

  private cacheTile(cacheKey: string, features: CafeFeature[]): void {
    this.tileCache.delete(cacheKey);
    this.tileCache.set(cacheKey, {
      expiresAtMs: Date.now() + TILE_CACHE_TTL_MS,
      features,
    });
    while (this.tileCache.size > MAX_CACHED_TILES) {
      const oldestKey = this.tileCache.keys().next().value as string | undefined;
      if (oldestKey === undefined) break;
      this.tileCache.delete(oldestKey);
    }
  }

  private async withRequestSlot<T>(
    signal: AbortSignal,
    operation: () => Promise<T>,
  ): Promise<T> {
    await this.acquireRequestSlot(signal);
    try {
      signal.throwIfAborted();
      return await operation();
    } finally {
      this.activeRequestCount -= 1;
      this.requestWaiters.shift()?.();
    }
  }

  private acquireRequestSlot(signal: AbortSignal): Promise<void> {
    signal.throwIfAborted();
    if (this.activeRequestCount < MAX_CONCURRENT_REQUESTS) {
      this.activeRequestCount += 1;
      return Promise.resolve();
    }

    return new Promise<void>((resolve, reject) => {
      let settled = false;
      const start = (): void => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", abort);
        this.activeRequestCount += 1;
        resolve();
      };
      const abort = (): void => {
        if (settled) return;
        settled = true;
        const waiterIndex = this.requestWaiters.indexOf(start);
        if (waiterIndex >= 0) this.requestWaiters.splice(waiterIndex, 1);
        reject(signal.reason);
      };
      this.requestWaiters.push(start);
      signal.addEventListener("abort", abort, { once: true });
    });
  }
}
