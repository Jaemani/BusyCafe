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

import {
  initializeCrowdFeedback,
  showCafePanel,
  updateOpenCafePanel,
} from "./panel";
import { initializeCafePanelSheet } from "./cafe-panel-sheet";

function panelMarkup(): string {
  return `
    <aside id="cafe-panel" hidden>
      <button id="cafe-panel-sheet-toggle" aria-expanded="false">상세</button>
      <h2 id="cafe-name"></h2><span id="cafe-address"></span><span id="cafe-phone"></span>
      <a id="cafe-website"></a><span id="cafe-level"></span>
      <span id="cafe-coverage"></span><span id="cafe-confidence"></span>
      <span id="estimate-dot"></span><p id="cafe-evidence"></p><p class="cafe-source" hidden><span id="cafe-source"></span></p>
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
        <a id="map-link-naver" hidden></a><a id="map-link-kakao" hidden></a>
        <button id="crowd-feedback-disclosure" aria-controls="crowd-feedback" aria-expanded="false" hidden>피드백 주기</button>
        <a id="map-link-google" hidden></a>
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
    initializeCafePanelSheet(
      document.querySelector<HTMLElement>("#cafe-panel")!,
      document.querySelector<HTMLButtonElement>("#cafe-panel-sheet-toggle")!,
    );
    initializeCrowdFeedback(contributionApi);
  });

  it("requires both answers, submits once, and resets for a new cafe", async () => {
    showCafePanel(cafe("first"));
    document.querySelector<HTMLButtonElement>(
      "#crowd-feedback-disclosure",
    )!.click();
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
    document.querySelector<HTMLButtonElement>(
      "#crowd-feedback-disclosure",
    )!.click();
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
    expect(document.querySelector("#cafe-source")?.textContent).toBe("카카오맵에서 확인한 장소");
    expect(document.querySelector<HTMLElement>(".cafe-source")?.hidden).toBe(true);
    expect(document.body.textContent).not.toContain("장소 원장 품질");
    expect(document.body.textContent).not.toContain("2026-07-14T12:33");
  });

  it("opens the sheet and focuses feedback from the visible panel action", () => {
    showCafePanel(cafe("compact"));

    const panel = document.querySelector<HTMLElement>("#cafe-panel")!;
    const disclosure = document.querySelector<HTMLButtonElement>(
      "#crowd-feedback-disclosure",
    )!;
    const feedback = document.querySelector<HTMLElement>("#crowd-feedback")!;
    expect(disclosure.hidden).toBe(false);
    expect(document.querySelector<HTMLElement>("#external-map-links")?.hidden).toBe(false);
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    expect(feedback.hidden).toBe(true);
    expect(panel.dataset.feedbackState).toBe("collapsed");

    disclosure.click();
    expect(panel.dataset.sheetState).toBe("expanded");
    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    expect(feedback.hidden).toBe(false);
    expect(panel.dataset.feedbackState).toBe("expanded");
    expect(panel.classList.contains("feedback-expanded")).toBe(true);
    expect(document.activeElement).toBe(
      document.querySelector('[data-street-feedback="similar"]'),
    );

    updateOpenCafePanel({
      ...cafe("compact"),
      observationAgeMinutes: 6,
    });
    expect(feedback.hidden).toBe(false);

    showCafePanel(cafe("next"));
    expect(feedback.hidden).toBe(true);
    expect(panel.dataset.feedbackState).toBe("collapsed");
  });

  it("returns the sheet to compact when a different cafe is selected", () => {
    showCafePanel(cafe("sheet-first"));
    const panel = document.querySelector<HTMLElement>("#cafe-panel")!;
    const toggle = document.querySelector<HTMLButtonElement>(
      "#cafe-panel-sheet-toggle",
    )!;

    toggle.click();
    expect(panel.dataset.sheetState).toBe("expanded");

    showCafePanel(cafe("sheet-second"));
    expect(panel.dataset.sheetState).toBe("compact");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("does not let incomplete or older summaries erase current detail evidence", () => {
    const detail = {
      ...cafe("stable"),
      name: "최신 상세 이름",
      hotspotName: "최신 관측 지역",
      distanceM: 321,
      observedAt: "2026-07-16T10:00:00Z",
    };
    showCafePanel(detail);

    updateOpenCafePanel({
      ...detail,
      name: "캐시된 요약 이름",
      level: null,
      confidence: null,
      freshness: "stale",
      hotspotName: null,
      distanceM: null,
      observedAt: null,
      observationAgeMinutes: 121,
    });
    expect(document.querySelector("#cafe-name")?.textContent).toBe(
      "최신 상세 이름",
    );
    expect(document.querySelector("#cafe-evidence")?.textContent).toBe(
      "최신 관측 지역 관측 기준 · 321m 거리",
    );
    expect(document.querySelector("#cafe-level")?.textContent).toBe(
      "현재 혼잡도를 표시하지 않아요",
    );

    updateOpenCafePanel({
      ...detail,
      level: 4,
      hotspotName: "과거 관측 지역",
      distanceM: 999,
      observedAt: "2026-07-16T09:55:00Z",
    });
    expect(document.querySelector("#cafe-evidence")?.textContent).toBe(
      "최신 관측 지역 관측 기준 · 321m 거리",
    );
    expect(document.querySelector("#cafe-level")?.textContent).toBe(
      "현재 혼잡도를 표시하지 않아요",
    );
  });
});
