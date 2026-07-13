import type { Feature, FeatureCollection, Point } from "geojson";

export interface CafeProperties {
  id: string;
  name: string;
  address: string;
  phone: string | null;
  website: string | null;
  lat: number;
  lng: number;
  sourceLabel: string;
  naverUrl: string | null;
  kakaoUrl: string | null;
  googleUrl: string | null;
  coverage: "covered" | "fringe" | "uncovered";
  level: 1 | 2 | 3 | 4 | null;
  confidence: number | null;
  confidenceTier: "high" | "mid" | "low" | null;
  freshness: "fresh" | "delayed" | "stale" | "n/a";
  hotspotName: string | null;
  distanceM: number | null;
  observedAt: string | null;
  observationAgeMinutes: number | null;
  observationAgeMeasuredAtMs: number;
}

export type CafeFeature = Feature<Point, CafeProperties>;
export type CafeFeatureCollection = FeatureCollection<Point, CafeProperties>;

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
}

export class EmptyCafeProvider implements CafeProvider {
  async getCafes(
    _viewport: CafeViewport,
    _signal: AbortSignal,
  ): Promise<CafeFeatureCollection> {
    return { type: "FeatureCollection", features: [] };
  }
}

interface CafeApiItem {
  id: string | number;
  name: string;
  lat: number;
  lng: number;
  road_address?: string | null;
  phone?: string | null;
  website?: string | null;
  source_label?: string | null;
  level?: 1 | 2 | 3 | 4 | null;
  confidence?: number | null;
  confidence_tier?: "high" | "mid" | "low" | null;
  freshness?: "fresh" | "delayed" | "stale" | "n/a";
  coverage?: "covered" | "fringe" | "uncovered";
  evidence?: {
    hotspot_name?: string | null;
    distance_m?: number | null;
    observed_at?: string | null;
    age_minutes?: number | null;
  } | null;
  external_links?: {
    naver?: string | null;
    kakao?: string | null;
    google?: string | null;
  } | null;
}

export class CachedApiCafeProvider implements CafeProvider {
  constructor(private readonly apiBaseUrl = "") {}

  async getCafes(
    viewport: CafeViewport,
    signal: AbortSignal,
  ): Promise<CafeFeatureCollection> {
    const bbox = [viewport.minLng, viewport.minLat, viewport.maxLng, viewport.maxLat].join(",");
    const url = new URL("/api/cafes", this.apiBaseUrl || window.location.origin);
    url.searchParams.set("bbox", bbox);
    url.searchParams.set("min_conf", "0");

    const response = await fetch(url, { signal });
    if (!response.ok) {
      throw new Error("검증된 카페 데이터 서버에 연결할 수 없습니다");
    }
    const items = (await response.json()) as CafeApiItem[];
    if (!Array.isArray(items)) throw new Error("카페 데이터 응답 형식이 올바르지 않습니다");
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
          address: item.road_address ?? "주소 정보 없음",
          phone: item.phone ?? null,
          website: item.website ?? null,
          lat: item.lat,
          lng: item.lng,
          sourceLabel: item.source_label ?? "서버 검증 카페 원장",
          naverUrl: item.external_links?.naver ?? null,
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
          observationAgeMinutes:
            typeof item.evidence?.age_minutes === "number" &&
            Number.isFinite(item.evidence.age_minutes) &&
            item.evidence.age_minutes >= 0
              ? item.evidence.age_minutes
              : null,
          observationAgeMeasuredAtMs,
        },
      }];
    });

    return { type: "FeatureCollection", features };
  }
}
