// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CafeContributionApi } from "./api";
import type { CafeProperties } from "./cafe-provider";

const analyticsMocks = vi.hoisted(() => ({
  trackCrowdFeedback: vi.fn(),
}));

vi.mock("./analytics", () => ({
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
        <button data-street-feedback="similar" aria-pressed="false">비슷해요</button>
        <button data-street-feedback="busier" aria-pressed="false">더 붐벼요</button>
        <button data-street-feedback="quieter" aria-pressed="false">더 한산해요</button>
        <button data-seat-feedback="available" aria-pressed="false">여유</button>
        <button data-seat-feedback="limited" aria-pressed="false">조금</button>
        <button data-seat-feedback="full" aria-pressed="false">만석</button>
        <button data-seat-feedback="not_entered" aria-pressed="false">안 들어감</button>
        <button id="crowd-feedback-submit" disabled>보내기</button>
        <p id="crowd-feedback-status"></p>
      </section>
      <details id="place-report" hidden>
        <button data-place-report="missing">카페 아님</button>
        <button data-place-report="wrong_details">정보 오류</button>
        <button data-place-report="closed">폐업</button>
        <p id="place-report-status"></p>
      </details>
      <nav id="external-map-links" hidden>
        <a id="map-link-naver"></a><a id="map-link-kakao"></a><a id="map-link-google"></a>
      </nav>
    </aside>`;
}

function createContributionApi(): CafeContributionApi & {
  submitCafeFeedback: ReturnType<typeof vi.fn>;
  reportCafe: ReturnType<typeof vi.fn>;
  reportMissingCafe: ReturnType<typeof vi.fn>;
} {
  return {
    submitCafeFeedback: vi.fn().mockResolvedValue(undefined),
    reportCafe: vi.fn().mockResolvedValue(undefined),
    reportMissingCafe: vi.fn().mockResolvedValue(undefined),
  };
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
  let contributionApi: ReturnType<typeof createContributionApi>;

  beforeEach(() => {
    document.body.innerHTML = panelMarkup();
    analyticsMocks.trackCrowdFeedback.mockClear();
    contributionApi = createContributionApi();
    initializeCrowdFeedback(contributionApi);
  });

  it("requires both answers, submits once, and resets for a new cafe", async () => {
    showCafePanel(cafe("first"));
    const quieter = document.querySelector<HTMLButtonElement>(
      '[data-street-feedback="quieter"]',
    )!;
    const available = document.querySelector<HTMLButtonElement>(
      '[data-seat-feedback="available"]',
    )!;
    const submit = document.querySelector<HTMLButtonElement>(
      "#crowd-feedback-submit",
    )!;
    quieter.click();
    expect(submit.disabled).toBe(true);
    available.click();
    expect(submit.disabled).toBe(false);
    submit.click();
    submit.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(contributionApi.submitCafeFeedback).toHaveBeenCalledOnce();
    expect(contributionApi.submitCafeFeedback).toHaveBeenCalledWith(
      "first",
      "quieter",
      "available",
    );
    expect(analyticsMocks.trackCrowdFeedback).toHaveBeenCalledOnce();
    expect(analyticsMocks.trackCrowdFeedback).toHaveBeenCalledWith(
      "quieter",
      2,
      "covered",
    );
    expect(quieter.getAttribute("aria-pressed")).toBe("true");
    expect(quieter.disabled).toBe(true);
    expect(document.querySelector("#crowd-feedback-status")?.textContent).toContain(
      "고마워요",
    );

    showCafePanel(cafe("second"));
    const similar = document.querySelector<HTMLButtonElement>(
      '[data-street-feedback="similar"]',
    )!;
    expect(similar.disabled).toBe(false);
    expect(similar.getAttribute("aria-pressed")).toBe("false");
    expect(submit.disabled).toBe(true);
  });

  it("submits a selected-place report and locks duplicate clicks", async () => {
    showCafePanel(cafe("reported"));
    const details = document.querySelector<HTMLDetailsElement>("#place-report")!;
    expect(details.hidden).toBe(false);
    const closed = document.querySelector<HTMLButtonElement>(
      '[data-place-report="closed"]',
    )!;

    closed.click();
    closed.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(contributionApi.reportCafe).toHaveBeenCalledOnce();
    expect(contributionApi.reportCafe).toHaveBeenCalledWith("reported", "closed");
    expect(document.querySelector("#place-report-status")?.textContent).toContain(
      "접수했어요",
    );
    expect(closed.disabled).toBe(true);
  });

  it("shows a feedback error and enables retry", async () => {
    contributionApi.submitCafeFeedback.mockRejectedValueOnce(new Error("offline"));
    showCafePanel(cafe("retry"));
    const street = document.querySelector<HTMLButtonElement>(
      '[data-street-feedback="similar"]',
    )!;
    const seat = document.querySelector<HTMLButtonElement>(
      '[data-seat-feedback="not_entered"]',
    )!;
    const submit = document.querySelector<HTMLButtonElement>(
      "#crowd-feedback-submit",
    )!;
    street.click();
    seat.click();
    submit.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(document.querySelector("#crowd-feedback-status")?.textContent).toContain(
      "전송하지 못했어요",
    );
    expect(submit.disabled).toBe(false);
    expect(street.disabled).toBe(false);
  });

  it("hides model comparison when no current estimate is displayed", () => {
    showCafePanel({ ...cafe("uncovered"), level: null, freshness: "n/a" });

    expect(
      document.querySelector<HTMLElement>("#crowd-feedback")?.hidden,
    ).toBe(true);
    expect(
      document.querySelector<HTMLElement>("#place-report")?.hidden,
    ).toBe(false);

    showCafePanel({ ...cafe("stale"), level: null, freshness: "stale" });
    expect(
      document.querySelector<HTMLElement>("#crowd-feedback")?.hidden,
    ).toBe(true);
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
