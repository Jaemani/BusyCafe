// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const analyticsMocks = vi.hoisted(() => ({
  trackBrandFilter: vi.fn(),
  trackCafeSearchResult: vi.fn(),
  trackCafeSearchSelect: vi.fn(),
}));

vi.mock("./analytics", () => ({
  isAnalyticsCafeBrand: (brand: string) => [
    "스타벅스",
    "투썸플레이스",
    "메가MGC커피",
    "컴포즈커피",
    "빽다방",
    "이디야커피",
    "폴바셋",
  ].includes(brand),
  ...analyticsMocks,
}));

import {
  cafeMapCenter,
  cafeMatchesBrand,
  initializeCafeSearch,
  type CafeSearchApi,
  type CafeSearchResult,
} from "./cafe-search";

function searchMarkup(): string {
  return `
    <section>
      <form id="cafe-search-form">
        <input id="cafe-search-input" aria-expanded="false" />
        <button type="submit">검색</button>
      </form>
      <div id="cafe-brand-filters">
        <button type="button" data-cafe-brand="스타벅스" aria-pressed="false">스타벅스</button>
        <button type="button" data-cafe-brand="이디야커피" aria-pressed="false">이디야</button>
      </div>
      <div id="cafe-search-popover" hidden>
        <p id="cafe-search-message"></p>
        <ul id="cafe-search-results"></ul>
      </div>
    </section>`;
}

function result(id = "cafe-1"): CafeSearchResult {
  return {
    id,
    name: "스타벅스 성수점",
    address: "서울 성동구 성수동",
    lat: 37.54,
    lng: 127.05,
    coverage: "covered",
    level: 1,
    confidence: 0.7,
    freshness: "fresh",
    hotspotName: "성수카페거리",
    distanceM: 120,
    observedAt: null,
    observationAgeMinutes: 5,
    observationAgeMeasuredAtMs: Date.now(),
  };
}

function createApi(items: CafeSearchResult[] = [result()]): CafeSearchApi & {
  search: ReturnType<typeof vi.fn>;
} {
  return {
    search: vi.fn().mockResolvedValue(items),
  };
}

describe("cafe search", () => {
  beforeEach(() => {
    document.body.innerHTML = searchMarkup();
    analyticsMocks.trackBrandFilter.mockClear();
    analyticsMocks.trackCafeSearchResult.mockClear();
    analyticsMocks.trackCafeSearchSelect.mockClear();
  });

  it("debounces name search and opens selected cafe", async () => {
    vi.useFakeTimers();
    const api = createApi();
    const onSelect = vi.fn();
    initializeCafeSearch({
      api,
      debounceMs: 300,
      onSelect,
      onBrandChange: vi.fn(),
    });
    const input = document.querySelector<HTMLInputElement>("#cafe-search-input")!;

    input.value = "스";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(300);
    expect(api.search).not.toHaveBeenCalled();
    expect(document.querySelector("#cafe-search-message")?.textContent).toBe(
      "두 글자 이상 입력해 주세요",
    );

    input.value = "스타벅스";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(300);
    expect(api.search).toHaveBeenCalledWith("스타벅스", null, expect.any(AbortSignal));
    expect(document.querySelector("#cafe-search-results strong")?.textContent).toBe(
      "스타벅스 성수점",
    );
    expect(analyticsMocks.trackCafeSearchResult).toHaveBeenCalledWith(1, "text");

    document.querySelector<HTMLButtonElement>("[data-search-result-index]")!.click();
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "cafe-1" }));
    expect(analyticsMocks.trackCafeSearchSelect).toHaveBeenCalledWith("text");
    expect(input.value).toBe("스타벅스 성수점");
    expect(document.querySelector<HTMLElement>("#cafe-search-popover")?.hidden).toBe(true);
    vi.useRealTimers();
  });

  it("toggles brand filter and searches without a text query", async () => {
    const api = createApi();
    const onBrandChange = vi.fn();
    initializeCafeSearch({ api, onSelect: vi.fn(), onBrandChange });

    const chip = document.querySelector<HTMLButtonElement>(
      '[data-cafe-brand="스타벅스"]',
    )!;
    chip.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(chip.getAttribute("aria-pressed")).toBe("true");
    expect(onBrandChange).toHaveBeenCalledWith("스타벅스");
    expect(api.search).toHaveBeenCalledWith("", "스타벅스", expect.any(AbortSignal));
    expect(analyticsMocks.trackBrandFilter).toHaveBeenCalledWith("스타벅스", "on");

    chip.click();
    expect(chip.getAttribute("aria-pressed")).toBe("false");
    expect(onBrandChange).toHaveBeenLastCalledWith(null);
    expect(analyticsMocks.trackBrandFilter).toHaveBeenLastCalledWith("스타벅스", "off");
  });

  it("shows empty and error states without stale result rows", async () => {
    const api = createApi([]);
    initializeCafeSearch({ api, onSelect: vi.fn(), onBrandChange: vi.fn() });
    const form = document.querySelector<HTMLFormElement>("#cafe-search-form")!;
    const input = document.querySelector<HTMLInputElement>("#cafe-search-input")!;
    input.value = "없는카페";
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
    expect(document.querySelector("#cafe-search-message")?.textContent).toBe(
      "일치하는 카페를 찾지 못했어요",
    );

    api.search.mockRejectedValueOnce(new Error("검색 서버 점검 중"));
    input.value = "오류카페";
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
    expect(document.querySelector("#cafe-search-message")?.textContent).toBe(
      "검색 서버 점검 중",
    );
    expect(document.querySelectorAll("[data-search-result-index]")).toHaveLength(0);
  });
});

describe("brand matching", () => {
  it("supports common display-name variants without fuzzy matching unrelated cafes", () => {
    expect(cafeMatchesBrand("메가커피 서울역점", "메가MGC커피")).toBe(true);
    expect(cafeMatchesBrand("이디야 성수점", "이디야커피")).toBe(true);
    expect(cafeMatchesBrand("개인 카페 메가톤", "메가MGC커피")).toBe(false);
    expect(cafeMatchesBrand("개인 카페", null)).toBe(true);
  });

  it("passes MapLibre coordinates in longitude-latitude order", () => {
    expect(cafeMapCenter({ lat: 37.54, lng: 127.05 })).toEqual([127.05, 37.54]);
  });
});
