import type { CafeMapProperties } from "./cafe-provider";
import {
  isAnalyticsCafeBrand,
  trackBrandFilter,
  trackCafeSearchResult,
  trackCafeSearchSelect,
  type CafeSearchMode,
} from "./analytics";

const DEFAULT_DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 50;
const MIN_QUERY_LENGTH = 2;
const SERVER_ORIGIN_DECIMALS = 3;

export interface CafeDistanceOrigin {
  lat: number;
  lng: number;
  label?: string;
}

export interface CafeSearchResult extends CafeMapProperties {
  address: string;
  proximityDistanceM?: number;
}

export interface CafeSearchApi {
  search(
    query: string,
    brand: string | null,
    signal: AbortSignal,
    origin?: CafeDistanceOrigin,
  ): Promise<CafeSearchResult[]>;
}

export interface CafeSearchController {
  updateDistanceOrigin(origin: CafeDistanceOrigin): void;
  destroy(): void;
}

interface CafeSearchOptions {
  onSelect: (cafe: CafeSearchResult) => void;
  onBrandChange: (brand: string | null) => void;
  onResultsChange?: (cafes: CafeSearchResult[] | null) => void;
  distanceOrigin?: CafeDistanceOrigin;
  apiBaseUrl?: string;
  api?: CafeSearchApi;
  debounceMs?: number;
}

interface SearchApiItem {
  id?: string | number;
  name?: string;
  road_address?: string | null;
  lat?: number;
  lng?: number;
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

function requiredElement<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Required search element missing: ${selector}`);
  return element;
}

function validAge(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function parseSearchItem(item: SearchApiItem, measuredAtMs: number): CafeSearchResult | null {
  if (
    (typeof item.id !== "string" && typeof item.id !== "number") ||
    typeof item.name !== "string" ||
    item.name.trim() === "" ||
    typeof item.lat !== "number" ||
    typeof item.lng !== "number" ||
    !Number.isFinite(item.lat) ||
    !Number.isFinite(item.lng) ||
    item.lat < -90 ||
    item.lat > 90 ||
    item.lng < -180 ||
    item.lng > 180
  ) {
    return null;
  }

  return {
    id: String(item.id),
    name: item.name,
    address: item.road_address?.trim() || "주소 정보 없음",
    lat: item.lat,
    lng: item.lng,
    coverage: item.coverage ?? "uncovered",
    level: item.level ?? null,
    confidence: item.confidence ?? null,
    freshness: item.freshness ?? "n/a",
    hotspotName: item.evidence?.hotspot_name ?? null,
    distanceM: item.evidence?.distance_m ?? null,
    observedAt: item.evidence?.observed_at ?? null,
    observationAgeMinutes: validAge(
      item.age_minutes ?? item.evidence?.age_minutes,
    ),
    observationAgeMeasuredAtMs: measuredAtMs,
  };
}

class HttpCafeSearchApi implements CafeSearchApi {
  constructor(private readonly apiBaseUrl = "") {}

  async search(
    query: string,
    brand: string | null,
    signal: AbortSignal,
    origin?: CafeDistanceOrigin,
  ): Promise<CafeSearchResult[]> {
    const url = new URL(
      "/api/cafes/search",
      this.apiBaseUrl || window.location.origin,
    );
    if (query) url.searchParams.set("q", query);
    if (brand) url.searchParams.set("brand", brand);
    if (origin !== undefined) {
      url.searchParams.set("origin_lat", String(origin.lat));
      url.searchParams.set("origin_lng", String(origin.lng));
    }
    url.searchParams.set("limit", String(SEARCH_LIMIT));

    const response = await fetch(url, { signal, cache: "default" });
    if (!response.ok) throw new Error("검색 서버에 연결할 수 없습니다");
    const payload = (await response.json()) as unknown;
    if (!Array.isArray(payload)) throw new Error("검색 응답 형식이 올바르지 않습니다");

    const measuredAtMs = Date.now();
    return payload.flatMap((item): CafeSearchResult[] => {
      if (typeof item !== "object" || item === null) return [];
      const parsed = parseSearchItem(item as SearchApiItem, measuredAtMs);
      return parsed ? [parsed] : [];
    });
  }
}

function levelLabel(level: CafeSearchResult["level"]): string | null {
  return level === 1
    ? "주변 여유"
    : level === 2
      ? "주변 보통"
      : level === 3
        ? "주변 약간 붐빔"
        : level === 4
          ? "주변 붐빔"
          : null;
}

function searchMode(query: string, brand: string | null): CafeSearchMode {
  return query !== "" && brand !== null
    ? "both"
    : brand !== null
      ? "brand"
      : "text";
}

export function cafeMatchesBrand(name: string, brand: string | null): boolean {
  if (brand === null) return true;
  const normalized = name.replaceAll(" ", "").toLocaleLowerCase("ko-KR");
  const aliases: Record<string, string[]> = {
    스타벅스: ["스타벅스"],
    투썸플레이스: ["투썸플레이스", "투썸"],
    메가MGC커피: ["메가mgc커피", "메가커피"],
    컴포즈커피: ["컴포즈커피", "컴포즈"],
    빽다방: ["빽다방"],
    이디야커피: ["이디야커피", "이디야"],
    폴바셋: ["폴바셋"],
    더벤티: ["더벤티", "theventi"],
    매머드커피: ["매머드커피", "매머드익스프레스", "매머드", "mammothcoffee"],
    텐퍼센트커피: ["텐퍼센트커피", "텐퍼센트", "10퍼센트커피", "10%커피", "tenpercentcoffee"],
    할리스: ["할리스커피", "할리스", "hollyscoffee", "hollys"],
    탐앤탐스: ["탐앤탐스커피", "탐앤탐스", "tomntoms", "tomtomcoffee"],
    카페베네: ["카페베네", "caffebene"],
    커피빈: ["커피빈코리아", "커피빈", "thecoffeebean&tealeaf", "coffeebean"],
    엔제리너스: ["엔제리너스커피", "엔제리너스", "angel-in-us", "angelinus"],
  };
  return (aliases[brand] ?? [brand]).some((alias) =>
    normalized.includes(alias.replaceAll(" ", "").toLocaleLowerCase("ko-KR"))
  );
}

export function cafeMapCenter(
  cafe: Pick<CafeSearchResult, "lat" | "lng">,
): [number, number] {
  return [cafe.lng, cafe.lat];
}

export function cafeDistanceMeters(
  cafe: CafeDistanceOrigin,
  origin: CafeDistanceOrigin,
): number {
  const earthRadiusM = 6_371_000;
  const toRadians = (degrees: number): number => degrees * Math.PI / 180;
  const lat1 = toRadians(origin.lat);
  const lat2 = toRadians(cafe.lat);
  const deltaLat = lat2 - lat1;
  const deltaLng = toRadians(cafe.lng - origin.lng);
  const haversine = Math.sin(deltaLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLng / 2) ** 2;
  return 2 * earthRadiusM * Math.asin(Math.sqrt(Math.min(1, haversine)));
}

export function coarseSearchOrigin(
  origin: CafeDistanceOrigin,
): CafeDistanceOrigin {
  const scale = 10 ** SERVER_ORIGIN_DECIMALS;
  return {
    lat: Math.round(origin.lat * scale) / scale,
    lng: Math.round(origin.lng * scale) / scale,
  };
}

export function rankCafeSearchResults(
  cafes: CafeSearchResult[],
  origin: CafeDistanceOrigin,
): CafeSearchResult[] {
  return cafes
    .map((cafe) => ({
      ...cafe,
      proximityDistanceM: cafeDistanceMeters(cafe, origin),
    }))
    .sort((left, right) =>
      (left.proximityDistanceM ?? Number.POSITIVE_INFINITY) -
        (right.proximityDistanceM ?? Number.POSITIVE_INFINITY) ||
      left.id.localeCompare(right.id)
    );
}

export function formatCafeDistance(distanceM: number): string {
  if (distanceM < 10) return "10m 이내";
  if (distanceM < 1_000) {
    return `${Math.round(distanceM / 10) * 10}m`;
  }
  const distanceKm = distanceM / 1_000;
  return `${distanceKm < 10 ? distanceKm.toFixed(1) : Math.round(distanceKm)}km`;
}

export function initializeCafeSearch(options: CafeSearchOptions): CafeSearchController {
  const form = requiredElement<HTMLFormElement>("#cafe-search-form");
  const input = requiredElement<HTMLInputElement>("#cafe-search-input");
  const filters = requiredElement<HTMLElement>("#cafe-brand-filters");
  const popover = requiredElement<HTMLElement>("#cafe-search-popover");
  const message = requiredElement<HTMLElement>("#cafe-search-message");
  const resultList = requiredElement<HTMLUListElement>("#cafe-search-results");
  const api = options.api ?? new HttpCafeSearchApi(options.apiBaseUrl);
  const debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS;

  let selectedBrand: string | null = null;
  let controller: AbortController | null = null;
  let timerId: number | null = null;
  let requestSequence = 0;
  let results: CafeSearchResult[] = [];
  let resultMode: CafeSearchMode = "text";
  let distanceOrigin = options.distanceOrigin;

  const setPopoverOpen = (open: boolean): void => {
    popover.hidden = !open;
    input.setAttribute("aria-expanded", String(open));
  };

  const clearResults = (): void => {
    results = [];
    resultList.replaceChildren();
  };

  const cancelPending = (): void => {
    if (timerId !== null) window.clearTimeout(timerId);
    timerId = null;
    controller?.abort();
    controller = null;
    requestSequence += 1;
  };

  const renderResults = (items: CafeSearchResult[], publish = true): void => {
    clearResults();
    results = distanceOrigin === undefined
      ? [...items]
      : rankCafeSearchResults(items, distanceOrigin);
    if (publish) options.onResultsChange?.(results.length > 0 ? results : null);
    for (const [index, cafe] of results.entries()) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      const heading = document.createElement("strong");
      const address = document.createElement("span");
      const metadata = document.createElement("div");
      const crowd = levelLabel(cafe.level);

      button.type = "button";
      button.dataset.searchResultIndex = String(index);
      heading.textContent = cafe.name;
      address.textContent = cafe.address;
      button.append(heading, address);
      if (cafe.proximityDistanceM !== undefined) {
        const proximity = document.createElement("em");
        proximity.textContent = formatCafeDistance(cafe.proximityDistanceM);
        proximity.setAttribute("aria-label", `기준 위치에서 ${proximity.textContent}`);
        metadata.append(proximity);
      }
      if (crowd !== null) {
        const crowdLabel = document.createElement("small");
        crowdLabel.textContent = crowd;
        crowdLabel.dataset.level = String(cafe.level);
        metadata.append(crowdLabel);
      }
      if (metadata.childElementCount > 0) button.append(metadata);
      item.append(button);
      resultList.append(item);
    }
  };

  const resultCountMessage = (): string => {
    const count = results.length.toLocaleString("ko-KR");
    return distanceOrigin === undefined
      ? `${count}곳을 찾았어요`
      : `${count}곳 · ${distanceOrigin.label ?? "기준 위치"}에서 가까운 순`;
  };

  const runSearch = async (): Promise<void> => {
    timerId = null;
    const query = input.value.trim();
    if (selectedBrand === null && query.length < MIN_QUERY_LENGTH) {
      cancelPending();
      clearResults();
      options.onResultsChange?.(null);
      if (query.length > 0) {
        message.textContent = "두 글자 이상 입력해 주세요";
        setPopoverOpen(true);
      } else {
        message.textContent = "";
        setPopoverOpen(false);
      }
      return;
    }

    controller?.abort();
    const activeController = new AbortController();
    controller = activeController;
    const sequence = ++requestSequence;
    const mode = searchMode(query, selectedBrand);
    message.textContent = "카페를 찾는 중…";
    clearResults();
    options.onResultsChange?.(null);
    setPopoverOpen(true);

    try {
      const items = distanceOrigin === undefined
        ? await api.search(query, selectedBrand, activeController.signal)
        : await api.search(
            query,
            selectedBrand,
            activeController.signal,
            coarseSearchOrigin(distanceOrigin),
          );
      if (activeController.signal.aborted || sequence !== requestSequence) return;
      resultMode = mode;
      trackCafeSearchResult(items.length, mode);
      renderResults(items);
      message.textContent = items.length === 0
        ? "일치하는 카페를 찾지 못했어요"
        : resultCountMessage();
    } catch (error) {
      if (activeController.signal.aborted || sequence !== requestSequence) return;
      clearResults();
      options.onResultsChange?.(null);
      message.textContent = error instanceof Error
        ? error.message
        : "검색 결과를 불러오지 못했습니다";
    } finally {
      if (controller === activeController) controller = null;
    }
  };

  const scheduleSearch = (): void => {
    if (timerId !== null) window.clearTimeout(timerId);
    timerId = window.setTimeout(() => void runSearch(), debounceMs);
  };

  const onInput = (): void => {
    if (selectedBrand === null && input.value.trim() === "") {
      if (timerId !== null) window.clearTimeout(timerId);
      void runSearch();
      return;
    }
    scheduleSearch();
  };
  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    if (timerId !== null) window.clearTimeout(timerId);
    void runSearch();
  };
  const onFilterClick = (event: MouseEvent): void => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest<HTMLButtonElement>("[data-cafe-brand]");
    if (!button || !filters.contains(button)) return;
    const brand = button.dataset.cafeBrand ?? null;
    const previousBrand = selectedBrand;
    selectedBrand = selectedBrand === brand ? null : brand;
    if (
      previousBrand !== null &&
      previousBrand !== selectedBrand &&
      isAnalyticsCafeBrand(previousBrand)
    ) {
      trackBrandFilter(previousBrand, "off");
    }
    if (
      selectedBrand !== null &&
      previousBrand !== selectedBrand &&
      isAnalyticsCafeBrand(selectedBrand)
    ) {
      trackBrandFilter(selectedBrand, "on");
    }
    for (const candidate of filters.querySelectorAll<HTMLButtonElement>("[data-cafe-brand]")) {
      candidate.setAttribute(
        "aria-pressed",
        String(candidate.dataset.cafeBrand === selectedBrand),
      );
    }
    options.onBrandChange(selectedBrand);
    if (timerId !== null) window.clearTimeout(timerId);
    void runSearch();
  };
  const onResultClick = (event: MouseEvent): void => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest<HTMLButtonElement>("[data-search-result-index]");
    const index = Number(button?.dataset.searchResultIndex);
    if (!button || !Number.isInteger(index) || !results[index]) return;
    input.value = results[index].name;
    setPopoverOpen(false);
    trackCafeSearchSelect(resultMode);
    options.onSelect(results[index]);
  };
  const onDocumentPointerDown = (event: PointerEvent): void => {
    const target = event.target;
    if (target instanceof Node && !form.parentElement?.contains(target)) {
      setPopoverOpen(false);
    }
  };
  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === "Escape") {
      setPopoverOpen(false);
      input.focus();
    }
  };

  input.addEventListener("input", onInput);
  input.addEventListener("focus", () => {
    if (message.textContent) setPopoverOpen(true);
  });
  form.addEventListener("submit", onSubmit);
  filters.addEventListener("click", onFilterClick);
  resultList.addEventListener("click", onResultClick);
  document.addEventListener("pointerdown", onDocumentPointerDown);
  document.addEventListener("keydown", onKeyDown);

  return {
    updateDistanceOrigin(origin: CafeDistanceOrigin): void {
      distanceOrigin = origin;
      if (results.length > 0) {
        renderResults(results);
        message.textContent = resultCountMessage();
      }
    },
    destroy(): void {
      cancelPending();
      input.removeEventListener("input", onInput);
      form.removeEventListener("submit", onSubmit);
      filters.removeEventListener("click", onFilterClick);
      resultList.removeEventListener("click", onResultClick);
      document.removeEventListener("pointerdown", onDocumentPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    },
  };
}
