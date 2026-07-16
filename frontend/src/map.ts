import maplibregl, {
  type GeoJSONSource,
  type MapGeoJSONFeature,
} from "maplibre-gl";
import type {
  CafeFeatureCollection,
  CafeMapProperties,
  CafeProvider,
  CafeViewport,
} from "./cafe-provider";
import { CachedApiCafeProvider } from "./cafe-provider";
import {
  getOpenCafeId,
  hideCafePanel,
  showCafePanel,
  showCafePanelError,
  showCafePanelLoading,
  updateOpenCafePanel,
} from "./panel";
import {
  ageCafeCollection,
  hasVisualFreshnessChange,
  type FreshnessLimits,
} from "./observation-age";
import {
  evaluateRuntimeHealth,
  fetchRuntimeHealth,
  type RuntimeHealthState,
} from "./runtime-health";
import {
  trackCafeDetailError,
  trackCafeMarkerClick,
  trackGeolocationClick,
  trackGeolocationResult,
  trackViewportLoad,
} from "./analytics";
import {
  cafeMapCenter,
  initializeCafeSearch,
  type CafeDistanceOrigin,
  type CafeSearchController,
  type CafeSearchResult,
} from "./cafe-search";
import { addSeoulSubwayOverlay } from "./subway-overlay";
import { initializeAppViewport } from "./visual-viewport";

const MAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const INITIAL_CENTER: [number, number] = [126.9237, 37.5563];
const INITIAL_ZOOM = 14.5;
const CAFE_SOURCE = "cafes";
const CLUSTER_LAYER = "cafe-clusters";
const CLUSTER_COUNT_LAYER = "cafe-cluster-count";
const CAFE_LAYER = "cafe-points";
const CAFE_HIT_LAYER = "cafe-hit-area";
const MIN_CAFE_ZOOM = 11;
const DELAY_TICK_MS = 30_000;
const CAFE_BACKGROUND_REFRESH_MS = 5 * 60_000;
const RESUME_REFRESH_MIN_AGE_MS = 60_000;
const APP_VIEWPORT_RESIZE_EVENT = "busyCafeViewportResize";

const EMPTY_COLLECTION: CafeFeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

function searchResultCollection(
  cafes: CafeSearchResult[],
): CafeFeatureCollection {
  return {
    type: "FeatureCollection",
    features: cafes.map((cafe) => ({
      type: "Feature",
      id: cafe.id,
      geometry: { type: "Point", coordinates: [cafe.lng, cafe.lat] },
      properties: cafe,
    })),
  };
}

function readCafeProperties(feature: MapGeoJSONFeature): CafeMapProperties | null {
  const properties = feature.properties as Partial<CafeMapProperties> | null;
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
    lat,
    lng,
    coverage: properties.coverage ?? "uncovered",
    level: properties.level ?? null,
    confidence: properties.confidence ?? null,
    freshness: properties.freshness ?? "n/a",
    hotspotName: properties.hotspotName ?? null,
    distanceM: properties.distanceM ?? null,
    observedAt: properties.observedAt ?? null,
    observationAgeMinutes: properties.observationAgeMinutes ?? null,
    observationAgeMeasuredAtMs:
      typeof properties.observationAgeMeasuredAtMs === "number"
        ? properties.observationAgeMeasuredAtMs
        : Date.now(),
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
      delayed_count: [
        "+",
        ["case", ["==", ["get", "freshness"], "delayed"], 1, 0],
      ],
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
      "circle-opacity": [
        "case",
        ["==", ["get", "delayed_count"], ["get", "point_count"]],
        0.66,
        0.92,
      ],
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
    paint: {
      "text-color": "#ffffff",
      "text-opacity": [
        "case",
        ["==", ["get", "delayed_count"], ["get", "point_count"]],
        0.74,
        1,
      ],
    },
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
        ["==", ["get", "freshness"], "delayed"],
        0.55,
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

function bindInteractions(
  map: maplibregl.Map,
  onCafeSelect: (cafeId: string) => void,
): void {
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
    if (cafe) {
      trackCafeMarkerClick(
        cafe.coverage,
        cafe.level !== null && cafe.freshness !== "stale",
      );
      onCafeSelect(cafe.id);
    }
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
  const viewportController = initializeAppViewport();
  const stopViewportResize = viewportController.subscribe(() => {
    map.resize({ [APP_VIEWPORT_RESIZE_EVENT]: true });
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
  container
    .querySelector<HTMLButtonElement>(".maplibregl-ctrl-geolocate")
    ?.addEventListener("click", trackGeolocationClick);
  const attributionControl = new maplibregl.AttributionControl({ compact: true });
  map.addControl(attributionControl, "bottom-right");

  const cafeProvider = provider ?? new CachedApiCafeProvider();

  let searchController: CafeSearchController | null = null;
  let userDistanceOrigin: CafeDistanceOrigin | null = null;

  geolocateControl.on("geolocate", (event) => {
    if (
      Number.isFinite(event.coords.latitude) &&
      Number.isFinite(event.coords.longitude)
    ) {
      userDistanceOrigin = {
        lat: event.coords.latitude,
        lng: event.coords.longitude,
        label: "내 위치",
      };
      searchController?.updateDistanceOrigin(userDistanceOrigin);
    }
    trackGeolocationResult("success");
    statusElement.textContent = "내 위치를 찾았습니다";
    statusElement.dataset.state = "ready";
  });
  geolocateControl.on("error", () => {
    trackGeolocationResult("error");
    statusElement.textContent = "위치 권한을 확인해 주세요";
    statusElement.dataset.state = "error";
  });

  let requestController: AbortController | null = null;
  let requestSequence = 0;
  let hasTrackedViewportLoad = false;
  let runtimeHealth: RuntimeHealthState | "unknown" | null = null;
  let freshnessLimits: FreshnessLimits | null = null;
  let displayedCafeCount: number | null = null;
  let displayedDelayedCount = 0;
  let displayedStaleCount = 0;
  let displayedDelayRange: { min: number; max: number } | null = null;
  let activeSearchResults: CafeSearchResult[] | null = null;

  const renderCafeStatus = (): void => {
    if (displayedCafeCount === null) return;
    const runtimeKind = runtimeHealth !== null && runtimeHealth !== "unknown"
      ? runtimeHealth.kind
      : null;
    const modeLabel = runtimeKind === "snapshot"
      ? " · 배포 스냅샷"
      : runtimeHealth === "unknown"
        ? " · 데이터 모드 확인 불가"
        : "";
    if (
      runtimeKind === "delayed" ||
      displayedDelayedCount > 0 ||
      displayedStaleCount > 0
    ) {
      const countLabel = displayedCafeCount > 0
        ? ` · 카페 ${displayedCafeCount.toLocaleString("ko-KR")}곳`
        : "";
      const laggedCount = displayedDelayedCount + displayedStaleCount;
      const partialLabel = laggedCount > 0 && laggedCount < displayedCafeCount
        ? "일부 데이터"
        : "데이터";
      const delayLabel = displayedDelayRange === null
        ? "갱신 지연 중"
        : displayedDelayRange.min === displayedDelayRange.max
          ? `${displayedDelayRange.min}분 전 관측`
          : `${displayedDelayRange.min}~${displayedDelayRange.max}분 전 관측`;
      statusElement.textContent = `${partialLabel} ${delayLabel}${countLabel}`;
      statusElement.dataset.state = "stale";
      return;
    }
    if (displayedCafeCount === 0) {
      statusElement.textContent = `지도 준비됨 · 카페 데이터 연결 대기${modeLabel}`;
      statusElement.dataset.state = "empty";
      return;
    }
    statusElement.textContent =
      `카페 ${displayedCafeCount.toLocaleString("ko-KR")}곳${modeLabel}`;
    statusElement.dataset.state = "ready";
  };

  let rawCafeCollection = EMPTY_COLLECTION;
  let displayedCafeCollection = EMPTY_COLLECTION;
  let hasLoadedCafeData = false;

  const refreshRuntimeHealth = async (): Promise<void> => {
    await fetchRuntimeHealth()
      .then((health) => {
        // Completed no-op polls advance operational health but not map data.
        // The latest durable snapshot fetch changes only when ingest inserts
        // a new natural-key observation, so duplicate polls reuse tile/detail
        // caches instead of forcing a full viewport reload.
        cafeProvider.setCacheVersion?.(
          `${health.lastIngestAt ?? "missing-ingest"}:${health.cafesCount}`,
        );
        runtimeHealth = evaluateRuntimeHealth(health);
        freshnessLimits = {
          freshMaxAgeMinutes: health.staleWarnMin,
          displayMaxAgeMinutes: health.currentDisplayMaxAgeMin,
        };
        updateDisplayedCollection();
        renderCafeStatus();
      })
      .catch(() => {
        runtimeHealth = "unknown";
        renderCafeStatus();
      });
  };

  const updateLegend = (hasScores: boolean): void => {
    const legend = document.querySelector<HTMLElement>("#map-legend");
    if (legend) legend.dataset.state = hasScores ? "scored" : "preview";
  };

  const updateDisplayedCollection = (forceMapUpdate = false): void => {
    if (!hasLoadedCafeData && activeSearchResults === null) return;
    const sourceCollection = activeSearchResults === null
      ? rawCafeCollection
      : searchResultCollection(activeSearchResults);
    const agedCollection = ageCafeCollection(
      sourceCollection,
      Date.now(),
      freshnessLimits,
    );
    const nextCollection = agedCollection;
    const shouldUpdateMap = forceMapUpdate ||
      hasVisualFreshnessChange(displayedCafeCollection, nextCollection);
    displayedCafeCollection = nextCollection;
    if (shouldUpdateMap) {
      const source = map.getSource(CAFE_SOURCE) as GeoJSONSource | undefined;
      source?.setData(displayedCafeCollection);
    }
    updateLegend(
      displayedCafeCollection.features.some((feature) => feature.properties.level !== null),
    );
    displayedCafeCount = displayedCafeCollection.features.length;
    const delayedFeatures = displayedCafeCollection.features.filter(
      (feature) => feature.properties.freshness === "delayed",
    );
    const delayedAges = delayedFeatures
      .map((feature) => feature.properties.observationAgeMinutes)
      .filter((age): age is number => age !== null && Number.isFinite(age))
      .map((age) => Math.ceil(age));
    displayedDelayedCount = delayedFeatures.length;
    displayedStaleCount = displayedCafeCollection.features.filter(
      (feature) => feature.properties.freshness === "stale",
    ).length;
    displayedDelayRange = delayedAges.length > 0
      ? { min: Math.min(...delayedAges), max: Math.max(...delayedAges) }
      : null;

    const openCafeId = getOpenCafeId();
    if (openCafeId !== null) {
      const openCafe = displayedCafeCollection.features.find(
        (feature) => feature.properties.id === openCafeId,
      );
      if (openCafe) updateOpenCafePanel(openCafe.properties);
      else hideCafePanel();
    }
    renderCafeStatus();
  };

  let detailController: AbortController | null = null;
  let detailRequestSequence = 0;
  const selectCafe = async (
    cafeId: string,
    fallback?: CafeMapProperties,
  ): Promise<void> => {
    const cafeFeature = displayedCafeCollection.features.find(
      (feature) => feature.properties.id === cafeId,
    );
    const cafe = cafeFeature?.properties ?? fallback;
    if (!cafe) return;

    detailController?.abort();
    const controller = new AbortController();
    detailController = controller;
    const sequence = ++detailRequestSequence;
    showCafePanelLoading(cafe);
    try {
      const detail = await cafeProvider.getCafeDetail(cafeId, controller.signal);
      if (
        controller.signal.aborted ||
        sequence !== detailRequestSequence ||
        getOpenCafeId() !== cafeId
      ) return;
      showCafePanel(detail);
    } catch (error) {
      if (controller.signal.aborted || sequence !== detailRequestSequence) return;
      trackCafeDetailError();
      showCafePanelError(
        cafe,
        error instanceof Error ? error.message : "카페 상세 정보를 불러오지 못했습니다",
      );
    } finally {
      if (detailController === controller) detailController = null;
    }
  };

  let lastCafeFetchStartedAt = 0;
  const refresh = async (background = false): Promise<void> => {
    if (background && requestController !== null) return;
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    lastCafeFetchStartedAt = Date.now();
    const sequence = ++requestSequence;
    if (!background) {
      displayedCafeCount = null;
      displayedDelayedCount = 0;
      displayedStaleCount = 0;
      displayedDelayRange = null;
      statusElement.textContent = "현재 화면을 확인하는 중";
      statusElement.dataset.state = "loading";
    }

    if (map.getZoom() < MIN_CAFE_ZOOM) {
      const source = map.getSource(CAFE_SOURCE) as GeoJSONSource | undefined;
      source?.setData(EMPTY_COLLECTION);
      updateLegend(false);
      rawCafeCollection = EMPTY_COLLECTION;
      displayedCafeCollection = EMPTY_COLLECTION;
      hasLoadedCafeData = false;
      requestController = null;
      statusElement.textContent = "카페를 보려면 지도를 확대해 주세요";
      statusElement.dataset.state = "empty";
      return;
    }

    try {
      const result = await cafeProvider.getCafes(currentViewport(map), controller.signal);
      if (sequence !== requestSequence) return;
      rawCafeCollection = result;
      hasLoadedCafeData = true;
      updateDisplayedCollection(true);
      if (!hasTrackedViewportLoad) {
        hasTrackedViewportLoad = true;
        trackViewportLoad(
          displayedCafeCollection.features.length,
          displayedCafeCollection.features.filter(
            (feature) =>
              feature.properties.level !== null &&
              feature.properties.freshness !== "stale",
          ).length,
        );
      }
    } catch (error) {
      if (controller.signal.aborted || sequence !== requestSequence) return;
      if (!background || !hasLoadedCafeData) {
        statusElement.textContent =
          error instanceof Error ? error.message : "카페 데이터를 불러오지 못했습니다";
        statusElement.dataset.state = "error";
      }
    } finally {
      if (requestController === controller) requestController = null;
    }
  };

  map.on("movestart", (event) => {
    if ((event as unknown as Record<string, unknown>)[APP_VIEWPORT_RESIZE_EVENT] === true) {
      return;
    }
    requestController?.abort();
    requestSequence += 1;
    detailController?.abort();
    detailRequestSequence += 1;
    hideCafePanel();
    displayedCafeCount = null;
    displayedDelayedCount = 0;
    displayedStaleCount = 0;
    displayedDelayRange = null;
    hasLoadedCafeData = false;
    statusElement.textContent = "지도 이동 중";
    statusElement.dataset.state = "moving";
  });
  map.on("moveend", (event) => {
    if ((event as unknown as Record<string, unknown>)[APP_VIEWPORT_RESIZE_EVENT] === true) {
      return;
    }
    if (userDistanceOrigin === null) {
      const center = map.getCenter();
      searchController?.updateDistanceOrigin({
        lat: center.lat,
        lng: center.lng,
        label: "지도 중심",
      });
    }
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
      bindInteractions(map, selectCafe);
      void addSeoulSubwayOverlay(map, { beforeLayerId: CLUSTER_LAYER });
      void refreshRuntimeHealth()
        .then(() => refresh())
        .then(resolve, reject);
    });
    map.on("error", () => {
      if (map.loaded()) return;
      statusElement.textContent = "일부 지도 데이터를 다시 불러오는 중입니다";
      statusElement.dataset.state = "loading";
    });
  });

  const focusSearchResult = (cafe: CafeSearchResult): void => {
    let opened = false;
    const openCafe = (): void => {
      if (opened) return;
      opened = true;
      void selectCafe(cafe.id, cafe);
    };
    map.once("moveend", openCafe);
    map.easeTo({
      center: cafeMapCenter(cafe),
      zoom: Math.max(map.getZoom(), 16),
      duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 450,
    });
    window.setTimeout(openCafe, 700);
  };

  const initialCenter = map.getCenter();
  searchController = initializeCafeSearch({
    onSelect: focusSearchResult,
    onBrandChange: () => undefined,
    onResultsChange: (cafes) => {
      activeSearchResults = cafes;
      updateDisplayedCollection(true);
    },
    distanceOrigin: {
      lat: initialCenter.lat,
      lng: initialCenter.lng,
      label: "지도 중심",
    },
  });

  const refreshInBackground = (): void => {
    if (requestController !== null) return;
    void refreshRuntimeHealth().then(() => refresh(true));
  };

  const delayTimerId = window.setInterval(() => {
    if (map.isMoving()) return;
    updateDisplayedCollection();
    if (
      !document.hidden &&
      Date.now() - lastCafeFetchStartedAt >= CAFE_BACKGROUND_REFRESH_MS
    ) {
      refreshInBackground();
    }
  }, DELAY_TICK_MS);

  const refreshAfterResume = (): void => {
    if (
      document.hidden ||
      Date.now() - lastCafeFetchStartedAt < RESUME_REFRESH_MIN_AGE_MS
    ) return;
    refreshInBackground();
  };
  document.addEventListener("visibilitychange", refreshAfterResume);
  window.addEventListener("focus", refreshAfterResume);
  map.once("remove", () => {
    window.clearInterval(delayTimerId);
    requestController?.abort();
    detailController?.abort();
    searchController?.destroy();
    document.removeEventListener("visibilitychange", refreshAfterResume);
    window.removeEventListener("focus", refreshAfterResume);
    stopViewportResize();
  });
}
