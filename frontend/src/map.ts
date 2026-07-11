import maplibregl, {
  type GeoJSONSource,
  type MapGeoJSONFeature,
} from "maplibre-gl";
import type {
  CafeFeatureCollection,
  CafeProperties,
  CafeProvider,
  CafeViewport,
} from "./cafe-provider";
import { CachedApiCafeProvider } from "./cafe-provider";
import { hideCafePanel, showCafePanel } from "./panel";

const MAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const INITIAL_CENTER: [number, number] = [126.9237, 37.5563];
const INITIAL_ZOOM = 14.5;
const CAFE_SOURCE = "cafes";
const CLUSTER_LAYER = "cafe-clusters";
const CLUSTER_COUNT_LAYER = "cafe-cluster-count";
const CAFE_LAYER = "cafe-points";
const CAFE_HIT_LAYER = "cafe-hit-area";
const MIN_CAFE_ZOOM = 11;
const IS_DEPLOYED_SNAPSHOT = import.meta.env.VITE_DATA_MODE === "snapshot";

const EMPTY_COLLECTION: CafeFeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

function readCafeProperties(feature: MapGeoJSONFeature): CafeProperties | null {
  const properties = feature.properties as Partial<CafeProperties> | null;
  if (
    !properties ||
    typeof properties.id !== "string" ||
    typeof properties.name !== "string" ||
    feature.geometry.type !== "Point"
  ) {
    return null;
  }

  const [lng, lat] = feature.geometry.coordinates;
  if (typeof lat !== "number" || typeof lng !== "number") return null;

  return {
    id: properties.id,
    name: properties.name,
    address: properties.address ?? "주소 정보 없음",
    phone: properties.phone ?? null,
    website: properties.website ?? null,
    lat,
    lng,
    sourceLabel: properties.sourceLabel ?? "장소 데이터 출처 미상",
    naverUrl: properties.naverUrl ?? null,
    kakaoUrl: properties.kakaoUrl ?? null,
    googleUrl: properties.googleUrl ?? null,
    coverage: properties.coverage ?? "uncovered",
    level: properties.level ?? null,
    confidence: properties.confidence ?? null,
    confidenceTier: properties.confidenceTier ?? null,
    hotspotName: properties.hotspotName ?? null,
    distanceM: properties.distanceM ?? null,
    observedAt: properties.observedAt ?? null,
  };
}

function currentViewport(map: maplibregl.Map): CafeViewport {
  const bounds = map.getBounds();
  return {
    minLng: bounds.getWest(),
    minLat: bounds.getSouth(),
    maxLng: bounds.getEast(),
    maxLat: bounds.getNorth(),
    zoom: map.getZoom(),
  };
}

function addCafeLayers(map: maplibregl.Map): void {
  map.addSource(CAFE_SOURCE, {
    type: "geojson",
    data: EMPTY_COLLECTION,
    cluster: true,
    clusterMaxZoom: 14,
    clusterRadius: 42,
    clusterProperties: {
      max_level: ["max", ["coalesce", ["get", "level"], 0]],
    },
  });

  map.addLayer({
    id: CLUSTER_LAYER,
    type: "circle",
    source: CAFE_SOURCE,
    filter: ["has", "point_count"],
    paint: {
      "circle-color": [
        "match",
        ["get", "max_level"],
        1,
        "#2d8b68",
        2,
        "#c5a53a",
        3,
        "#d9772f",
        4,
        "#c94b43",
        "#59635f",
      ],
      "circle-radius": ["step", ["get", "point_count"], 18, 30, 22, 100, 27],
      "circle-stroke-color": "rgba(255, 255, 255, 0.92)",
      "circle-stroke-width": 3,
      "circle-opacity": 0.92,
    },
  });

  map.addLayer({
    id: CLUSTER_COUNT_LAYER,
    type: "symbol",
    source: CAFE_SOURCE,
    filter: ["has", "point_count"],
    layout: {
      "text-field": ["get", "point_count_abbreviated"],
      "text-size": 11,
      "text-font": ["Noto Sans Regular"],
    },
    paint: { "text-color": "#ffffff" },
  });

  map.addLayer({
    id: CAFE_LAYER,
    type: "circle",
    source: CAFE_SOURCE,
    filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": [
        "match",
        ["get", "level"],
        1,
        "#2d8b68",
        2,
        "#c5a53a",
        3,
        "#d9772f",
        4,
        "#c94b43",
        "#727b77",
      ],
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 4, 15, 8, 18, 10],
      "circle-stroke-color": [
        "match",
        ["get", "coverage"],
        "fringe",
        "#263d34",
        "uncovered",
        "#b9bfbb",
        "#ffffff",
      ],
      "circle-stroke-width": [
        "match",
        ["get", "coverage"],
        "fringe",
        4,
        "uncovered",
        1,
        2,
      ],
      "circle-opacity": [
        "case",
        ["<", ["coalesce", ["get", "confidence"], 0], 0.3],
        0.62,
        0.95,
      ],
    },
  });

  map.addLayer({
    id: CAFE_HIT_LAYER,
    type: "circle",
    source: CAFE_SOURCE,
    filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": "rgba(0, 0, 0, 0.01)",
      "circle-radius": 22,
    },
  });
}

function bindInteractions(map: maplibregl.Map): void {
  map.on("click", CLUSTER_LAYER, async (event) => {
    const feature = event.features?.[0];
    const clusterId = feature?.properties?.cluster_id;
    const source = map.getSource(CAFE_SOURCE) as GeoJSONSource | undefined;
    if (!feature || typeof clusterId !== "number" || !source) return;

    const zoom = await source.getClusterExpansionZoom(clusterId);
    const coordinates = feature.geometry.type === "Point" ? feature.geometry.coordinates : null;
    if (!coordinates) return;
    map.easeTo({ center: [coordinates[0], coordinates[1]], zoom });
  });

  map.on("click", CAFE_HIT_LAYER, (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const cafe = readCafeProperties(feature);
    if (cafe) showCafePanel(cafe);
  });

  for (const layer of [CLUSTER_LAYER, CAFE_HIT_LAYER]) {
    map.on("mouseenter", layer, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", layer, () => {
      map.getCanvas().style.cursor = "";
    });
  }
}

export async function initializeCafeMap(
  statusElement: HTMLElement,
  provider?: CafeProvider,
): Promise<void> {
  const container = document.querySelector<HTMLElement>("#map");
  if (!container) throw new Error("지도 컨테이너가 없습니다");

  const map = new maplibregl.Map({
    container,
    style: MAP_STYLE,
    center: INITIAL_CENTER,
    zoom: INITIAL_ZOOM,
    minZoom: 9,
    maxZoom: 19,
    attributionControl: false,
    locale: {
      "NavigationControl.ZoomIn": "확대",
      "NavigationControl.ZoomOut": "축소",
      "GeolocateControl.FindMyLocation": "내 위치 찾기",
      "GeolocateControl.LocationNotAvailable": "현재 위치를 확인할 수 없음",
    },
  });

  map.addControl(
    new maplibregl.NavigationControl({ showCompass: false, visualizePitch: false }),
    "bottom-right",
  );
  const geolocateControl = new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true, timeout: 10_000 },
    fitBoundsOptions: { maxZoom: 16 },
    trackUserLocation: false,
    showAccuracyCircle: true,
    showUserLocation: true,
  });
  map.addControl(geolocateControl, "bottom-right");
  const attributionControl = new maplibregl.AttributionControl({ compact: true });
  map.addControl(attributionControl, "bottom-right");
  const attributionElement = map
    .getContainer()
    .querySelector<HTMLDetailsElement>(".maplibregl-ctrl-attrib");
  attributionElement?.classList.remove("maplibregl-compact-show");
  attributionElement?.removeAttribute("open");

  const cafeProvider = provider ?? new CachedApiCafeProvider();

  geolocateControl.on("geolocate", () => {
    statusElement.textContent = "내 위치를 찾았습니다";
    statusElement.dataset.state = "ready";
  });
  geolocateControl.on("error", () => {
    statusElement.textContent = "위치 권한을 확인해 주세요";
    statusElement.dataset.state = "error";
  });

  let requestController: AbortController | null = null;
  let requestSequence = 0;

  const updateLegend = (hasScores: boolean): void => {
    const legend = document.querySelector<HTMLElement>("#map-legend");
    if (legend) legend.dataset.state = hasScores ? "live" : "preview";
  };

  const refresh = async (): Promise<void> => {
    requestController?.abort();
    requestController = new AbortController();
    const sequence = ++requestSequence;
    statusElement.textContent = "현재 화면을 확인하는 중";
    statusElement.dataset.state = "loading";

    if (map.getZoom() < MIN_CAFE_ZOOM) {
      const source = map.getSource(CAFE_SOURCE) as GeoJSONSource | undefined;
      source?.setData(EMPTY_COLLECTION);
      updateLegend(false);
      statusElement.textContent = "카페를 보려면 지도를 확대해 주세요";
      statusElement.dataset.state = "empty";
      return;
    }

    try {
      const result = await cafeProvider.getCafes(currentViewport(map), requestController.signal);
      if (sequence !== requestSequence) return;
      const source = map.getSource(CAFE_SOURCE) as GeoJSONSource | undefined;
      source?.setData(result);
      updateLegend(result.features.some((feature) => feature.properties.level !== null));
      statusElement.textContent = result.features.length
        ? `카페 ${result.features.length.toLocaleString("ko-KR")}곳${
            IS_DEPLOYED_SNAPSHOT ? " · 배포 스냅샷" : ""
          }`
        : "지도 준비됨 · 카페 데이터 연결 대기";
      statusElement.dataset.state = result.features.length ? "ready" : "empty";
    } catch (error) {
      if (requestController.signal.aborted || sequence !== requestSequence) return;
      statusElement.textContent =
        error instanceof Error ? error.message : "카페 데이터를 불러오지 못했습니다";
      statusElement.dataset.state = "error";
    }
  };

  map.on("movestart", () => {
    hideCafePanel();
    statusElement.textContent = "지도 이동 중";
    statusElement.dataset.state = "moving";
  });
  map.on("moveend", () => {
    void refresh();
  });

  await new Promise<void>((resolve, reject) => {
    const timeoutId = window.setTimeout(
      () => reject(new Error("지도 스타일 로드 시간이 초과됐습니다")),
      15_000,
    );
    map.once("load", () => {
      window.clearTimeout(timeoutId);
      addCafeLayers(map);
      bindInteractions(map);
      void refresh().then(resolve, reject);
    });
    map.on("error", () => {
      if (map.loaded()) return;
      statusElement.textContent = "일부 지도 데이터를 다시 불러오는 중입니다";
      statusElement.dataset.state = "loading";
    });
  });
}
