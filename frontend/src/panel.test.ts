// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CafeProperties } from "./cafe-provider";

const analyticsMocks = vi.hoisted(() => ({
  trackCrowdFeedback: vi.fn(),
}));

vi.mock("./analytics", () => ({
  isCustomAnalyticsEnabled: () => true,
  trackCrowdFeedback: analyticsMocks.trackCrowdFeedback,
}));

import { initializeCrowdFeedback, showCafePanel } from "./panel";

function panelMarkup(): string {
  return `
    <aside id="cafe-panel" hidden>
      <h2 id="cafe-name"></h2><p id="cafe-address"></p><span id="cafe-phone"></span>
      <a id="cafe-website"></a><span id="cafe-level"></span>
      <span id="cafe-coverage"></span><span id="cafe-confidence"></span>
      <span id="estimate-dot"></span><p id="cafe-evidence"></p><p id="cafe-source"></p>
      <section id="crowd-feedback" hidden>
        <p id="crowd-feedback-prompt"></p>
        <button data-crowd-feedback="similar" aria-pressed="false">비슷해요</button>
        <button data-crowd-feedback="busier" aria-pressed="false">더 붐벼요</button>
        <button data-crowd-feedback="quieter" aria-pressed="false">더 한산해요</button>
      </section>
      <nav id="external-map-links" hidden>
        <a id="map-link-naver"></a><a id="map-link-kakao"></a><a id="map-link-google"></a>
      </nav>
    </aside>`;
}

function cafe(id: string): CafeProperties {
  return {
    id,
    name: "테스트 카페",
    address: "서울",
    phone: null,
    website: null,
    lat: 37.5,
    lng: 127,
    sourceLabel: "test",
    naverUrl: null,
    naverSearchUrl: null,
    kakaoUrl: null,
    googleUrl: null,
    coverage: "covered",
    level: 2,
    confidence: 0.8,
    confidenceTier: "high",
    freshness: "fresh",
    hotspotName: "테스트 지역",
    distanceM: 100,
    observedAt: null,
    observationAgeMinutes: 5,
    observationAgeMeasuredAtMs: Date.now(),
  };
}

describe("crowd feedback", () => {
  beforeEach(() => {
    document.body.innerHTML = panelMarkup();
    analyticsMocks.trackCrowdFeedback.mockClear();
    initializeCrowdFeedback();
  });

  it("accepts only one response and resets for a newly selected cafe", () => {
    showCafePanel(cafe("first"));
    const quieter = document.querySelector<HTMLButtonElement>(
      '[data-crowd-feedback="quieter"]',
    )!;
    quieter.click();
    quieter.click();

    expect(analyticsMocks.trackCrowdFeedback).toHaveBeenCalledOnce();
    expect(analyticsMocks.trackCrowdFeedback).toHaveBeenCalledWith(
      "quieter",
      2,
      "covered",
    );
    expect(quieter.getAttribute("aria-pressed")).toBe("true");
    expect(quieter.disabled).toBe(true);

    showCafePanel(cafe("second"));
    const similar = document.querySelector<HTMLButtonElement>(
      '[data-crowd-feedback="similar"]',
    )!;
    expect(similar.disabled).toBe(false);
    similar.click();
    expect(analyticsMocks.trackCrowdFeedback).toHaveBeenCalledTimes(2);
  });

  it("shows concise evidence once and hides raw catalog metadata", () => {
    const delayedCafe = {
      ...cafe("delayed"),
      sourceLabel:
        "카카오맵 등록 장소 · 2026-07-14T12:33:46.294683+00:00 · 장소 원장 품질 1.00",
      coverage: "fringe" as const,
      level: 1 as const,
      freshness: "delayed" as const,
      hotspotName: "뚝섬역",
      distanceM: 846,
      observationAgeMinutes: 34,
    };

    showCafePanel(delayedCafe);

    expect(document.querySelector("#cafe-level")?.textContent).toBe("여유로 추정돼요");
    expect(document.querySelector("#cafe-evidence")?.textContent).toBe(
      "뚝섬역 관측 기준 · 846m 거리",
    );
    expect(document.querySelector("#cafe-coverage")?.textContent).toBe("경계 지역");
    expect(document.querySelector("#cafe-confidence")?.textContent).toBe(
      "34분 전 · 참고용",
    );
    expect(document.querySelector("#cafe-source")?.textContent).toBe(
      "카카오맵에서 확인한 장소",
    );
    expect(document.body.textContent).not.toContain("장소 원장 품질");
    expect(document.body.textContent).not.toContain("2026-07-14T12:33");
  });
});
