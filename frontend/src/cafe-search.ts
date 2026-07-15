import type { CafeMapProperties } from "./cafe-provider";
import {
  isAnalyticsCafeBrand,
  trackBrandFilter,
  trackCafeSearchResult,
  trackCafeSearchSelect,
  type CafeSearchMode,
} from "./analytics";

const DEFAULT_DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 20;
const MIN_QUERY_LENGTH = 2;

export interface CafeSearchResult extends CafeMapProperties {
  address: string;
}

export interface CafeSearchApi {
  search(
    query: string,
    brand: string | null,
    signal: AbortSignal,
  ): Promise<CafeSearchResult[]>;
}

export interface CafeSearchController {
  destroy(): void;
}

interface CafeSearchOptions {
  onSelect: (cafe: CafeSearchResult) => void;
  onBrandChange: (brand: string | null) => void;
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
  ): Promise<CafeSearchResult[]> {
    const url = new URL(
      "/api/cafes/search",
      this.apiBaseUrl || window.location.origin,
    );
    if (query) url.searchParams.set("q", query);
    if (brand) url.searchParams.set("brand", brand);
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

  const renderResults = (items: CafeSearchResult[]): void => {
    clearResults();
    results = items;
    for (const [index, cafe] of items.entries()) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      const heading = document.createElement("strong");
      const address = document.createElement("span");
      const crowd = levelLabel(cafe.level);

      button.type = "button";
      button.dataset.searchResultIndex = String(index);
      heading.textContent = cafe.name;
      address.textContent = cafe.address;
      button.append(heading, address);
      if (crowd !== null) {
        const crowdLabel = document.createElement("small");
        crowdLabel.textContent = crowd;
        crowdLabel.dataset.level = String(cafe.level);
        button.append(crowdLabel);
      }
      item.append(button);
      resultList.append(item);
    }
  };

  const runSearch = async (): Promise<void> => {
    timerId = null;
    const query = input.value.trim();
    if (selectedBrand === null && query.length < MIN_QUERY_LENGTH) {
      cancelPending();
      clearResults();
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
    setPopoverOpen(true);

    try {
      const items = await api.search(query, selectedBrand, activeController.signal);
      if (activeController.signal.aborted || sequence !== requestSequence) return;
      resultMode = mode;
      trackCafeSearchResult(items.length, mode);
      renderResults(items);
      message.textContent = items.length === 0
        ? "일치하는 카페를 찾지 못했어요"
        : `${items.length.toLocaleString("ko-KR")}곳을 찾았어요`;
    } catch (error) {
      if (activeController.signal.aborted || sequence !== requestSequence) return;
      clearResults();
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

  const onInput = (): void => scheduleSearch();
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
