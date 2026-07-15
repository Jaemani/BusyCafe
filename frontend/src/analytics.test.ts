import { beforeEach, describe, expect, it, vi } from "vitest";

const analyticsMocks = vi.hoisted(() => ({
  inject: vi.fn(),
  track: vi.fn(),
}));

vi.mock("@vercel/analytics", () => analyticsMocks);

import {
  initializeProductAnalytics,
  isAnalyticsCafeBrand,
  stripAnalyticsUrlDetails,
  trackBrandFilter,
  trackCafeMarkerClick,
  trackCafeSearchResult,
  trackCafeSearchSelect,
  trackCrowdFeedback,
  trackExternalMapClick,
  trackGeolocationClick,
  trackGeolocationResult,
  trackMapReady,
  trackViewportLoad,
} from "./analytics";

describe("product analytics", () => {
  beforeEach(() => {
    analyticsMocks.inject.mockClear();
    analyticsMocks.track.mockClear();
    vi.stubEnv("VITE_ENABLE_CUSTOM_ANALYTICS", "false");
  });

  it("enables anonymous page views while removing query strings and fragments", () => {
    initializeProductAnalytics();
    const options = analyticsMocks.inject.mock.calls[0]?.[0];
    expect(options).toMatchObject({ framework: "vite", mode: "auto", debug: false });
    expect(options.beforeSend({
      type: "pageview",
      url: "https://busy-cafe.vercel.app/?cafe=secret#37.5,127",
    })).toEqual({
      type: "pageview",
      url: "https://busy-cafe.vercel.app/",
    });
    expect(stripAnalyticsUrlDetails({ type: "event", url: "not a url" })).toBeNull();
  });

  it("does not emit unavailable Hobby custom events", () => {
    trackMapReady();
    trackGeolocationClick();
    trackViewportLoad(20, 15);
    expect(analyticsMocks.track).not.toHaveBeenCalled();
  });

  it("emits only bounded, low-cardinality product properties when enabled", () => {
    vi.stubEnv("VITE_ENABLE_CUSTOM_ANALYTICS", "true");

    trackCafeMarkerClick("covered", true);
    trackExternalMapClick("naver", "search");
    trackGeolocationResult("success");
    trackViewportLoad(20.8, 25);
    trackCrowdFeedback("quieter", 3, "fringe");
    trackCafeSearchResult(0, "text");
    trackCafeSearchResult(5, "brand");
    trackCafeSearchResult(20, "both");
    trackCafeSearchSelect("both");
    trackBrandFilter("스타벅스", "on");

    expect(analyticsMocks.track.mock.calls).toEqual([
      ["cafe_marker_click", { coverage: "covered", colored: true }],
      ["external_map_click", { provider: "naver", link_type: "search" }],
      ["geolocation_result", { result: "success" }],
      ["viewport_load", { count: 20, colored: 20 }],
      ["crowd_feedback", {
        feedback: "quieter",
        context: "fringe:3",
      }],
      ["cafe_search_result", { result_bucket: "0", mode: "text" }],
      ["cafe_search_result", { result_bucket: "1-5", mode: "brand" }],
      ["cafe_search_result", { result_bucket: "6-20", mode: "both" }],
      ["cafe_search_select", { mode: "both" }],
      ["brand_filter", { brand: "스타벅스", state: "on" }],
    ]);
  });

  it("accepts only the fixed brand allowlist", () => {
    for (const brand of [
      "스타벅스",
      "투썸플레이스",
      "메가MGC커피",
      "컴포즈커피",
      "빽다방",
      "이디야커피",
      "폴바셋",
      "더벤티",
      "매머드커피",
      "텐퍼센트커피",
      "할리스",
      "탐앤탐스",
      "카페베네",
      "커피빈",
      "엔제리너스",
    ]) {
      expect(isAnalyticsCafeBrand(brand)).toBe(true);
    }
    expect(isAnalyticsCafeBrand("사용자 검색어")).toBe(false);
  });
});
