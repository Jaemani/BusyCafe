import type { CafeMapProperties, CafeProperties } from "./cafe-provider";
import {
  isCustomAnalyticsEnabled,
  trackCrowdFeedback,
  type CrowdFeedback,
} from "./analytics";

function requiredElement<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing UI element: ${selector}`);
  return element;
}

function formatEvidence(cafe: CafeMapProperties): string {
  if (!cafe.hotspotName || cafe.distanceM === null) {
    return "이 지역은 아직 혼잡도 근거가 연결되지 않았어요.";
  }
  const ageLabel = cafe.freshness !== "stale" && cafe.observationAgeMinutes !== null
    ? ` · ${Math.ceil(cafe.observationAgeMinutes).toLocaleString("ko-KR")}분 전 관측`
    : "";
  return `${cafe.hotspotName} 기준 · ${Math.round(cafe.distanceM).toLocaleString("ko-KR")}m${ageLabel}`;
}

const LEVEL_LABELS = ["데이터 없음", "여유", "보통", "약간 붐빔", "붐빔"] as const;
const COVERAGE_LABELS = {
  covered: "커버됨",
  fringe: "경계 지역 · 참고용",
  uncovered: "실시간 미커버",
} as const;
const EVIDENCE_STRENGTH_LABELS = {
  high: "높음",
  mid: "보통",
  low: "낮음",
} as const;

let openCafeId: string | null = null;
let openCafeDetail: CafeProperties | null = null;
let crowdFeedbackSubmitted = false;

function setExternalLink(selector: string, href: string | null): boolean {
  const link = requiredElement<HTMLAnchorElement>(selector);
  if (!href) {
    link.hidden = true;
    link.removeAttribute("href");
    delete link.dataset.analyticsLinkType;
    return false;
  }
  link.href = href;
  link.hidden = false;
  return true;
}

function setNaverLink(
  directUrl: string | null,
  searchUrl: string | null,
): boolean {
  const link = requiredElement<HTMLAnchorElement>("#map-link-naver");
  const href = directUrl ?? searchUrl;
  link.textContent = directUrl ? "네이버지도에서 보기" : "네이버맵 검색";
  const visible = setExternalLink("#map-link-naver", href);
  if (visible) link.dataset.analyticsLinkType = directUrl ? "direct" : "search";
  return visible;
}

function resetCrowdFeedback(): void {
  crowdFeedbackSubmitted = false;
  const section = requiredElement<HTMLElement>("#crowd-feedback");
  section.hidden = true;
  requiredElement<HTMLElement>("#crowd-feedback-prompt").textContent =
    "지금 주변 분위기와 비교해 주세요";
  section.querySelectorAll<HTMLButtonElement>("[data-crowd-feedback]").forEach(
    (button) => {
      button.disabled = false;
      button.setAttribute("aria-pressed", "false");
    },
  );
}

export function initializeCrowdFeedback(): void {
  const section = requiredElement<HTMLElement>("#crowd-feedback");
  if (!isCustomAnalyticsEnabled()) {
    section.hidden = true;
    return;
  }
  section.querySelectorAll<HTMLButtonElement>("[data-crowd-feedback]").forEach(
    (button) => {
      button.addEventListener("click", () => {
        const feedback = button.dataset.crowdFeedback;
        if (
          crowdFeedbackSubmitted ||
          openCafeDetail === null ||
          (feedback !== "similar" && feedback !== "busier" && feedback !== "quieter")
        ) return;

        crowdFeedbackSubmitted = true;
        trackCrowdFeedback(
          feedback as CrowdFeedback,
          openCafeDetail.level,
          openCafeDetail.coverage,
        );
        requiredElement<HTMLElement>("#crowd-feedback-prompt").textContent =
          "피드백을 반영했어요";
        section.querySelectorAll<HTMLButtonElement>("[data-crowd-feedback]").forEach(
          (candidate) => {
            candidate.disabled = true;
            candidate.setAttribute(
              "aria-pressed",
              candidate === button ? "true" : "false",
            );
          },
        );
      });
    },
  );
}

function renderCrowdEstimate(
  cafe: CafeMapProperties,
  confidenceTier: CafeProperties["confidenceTier"] | null,
): void {
  requiredElement<HTMLElement>("#cafe-level").textContent =
    cafe.freshness === "stale"
      ? "갱신 지연 · 현재 혼잡도 숨김"
      : cafe.level === null
        ? LEVEL_LABELS[0]
        : LEVEL_LABELS[cafe.level];
  requiredElement<HTMLElement>("#cafe-coverage").textContent =
    COVERAGE_LABELS[cafe.coverage];
  requiredElement<HTMLElement>("#cafe-confidence").textContent =
    cafe.freshness === "stale"
      ? "오래된 근거 · 현재값 미표시"
      : cafe.freshness === "delayed"
        ? cafe.observationAgeMinutes === null
          ? "지연 데이터 · 참고용"
          : `${Math.ceil(cafe.observationAgeMinutes).toLocaleString("ko-KR")}분 지연 · 참고용`
      : confidenceTier === null
        ? "근거 강도 산정 전"
        : `근거 강도 ${EVIDENCE_STRENGTH_LABELS[confidenceTier]}`;
  requiredElement<HTMLElement>("#estimate-dot").dataset.level = String(cafe.level ?? 0);
  requiredElement<HTMLElement>("#cafe-evidence").textContent = formatEvidence(cafe);
}

function resetExternalLinks(): void {
  setNaverLink(null, null);
  setExternalLink("#map-link-kakao", null);
  setExternalLink("#map-link-google", null);
  requiredElement<HTMLElement>("#external-map-links").hidden = true;
}

function showPanelShell(cafe: CafeMapProperties): void {
  requiredElement<HTMLElement>("#cafe-name").textContent = cafe.name;
  renderCrowdEstimate(cafe, null);
  requiredElement<HTMLElement>("#cafe-panel").hidden = false;
  document.body.classList.add("panel-open");
}

function renderCafePanel(cafe: CafeProperties): void {
  requiredElement<HTMLElement>("#cafe-name").textContent = cafe.name;
  requiredElement<HTMLElement>("#cafe-address").textContent = cafe.address;
  requiredElement<HTMLElement>("#cafe-phone").textContent = cafe.phone ?? "전화 정보 없음";
  const website = requiredElement<HTMLAnchorElement>("#cafe-website");
  if (cafe.website) {
    website.href = cafe.website;
    website.hidden = false;
  } else {
    website.hidden = true;
    website.removeAttribute("href");
  }
  renderCrowdEstimate(cafe, cafe.confidenceTier);
  requiredElement<HTMLElement>("#cafe-source").textContent =
    `${cafe.sourceLabel} · 원본과 검증 상태를 함께 표시합니다.`;
  const hasExternalLink = [
    setNaverLink(cafe.naverUrl, cafe.naverSearchUrl),
    setExternalLink("#map-link-kakao", cafe.kakaoUrl),
    setExternalLink("#map-link-google", cafe.googleUrl),
  ].some(Boolean);
  const kakaoLink = requiredElement<HTMLAnchorElement>("#map-link-kakao");
  if (!kakaoLink.hidden) kakaoLink.dataset.analyticsLinkType = "direct";
  const googleLink = requiredElement<HTMLAnchorElement>("#map-link-google");
  if (!googleLink.hidden) googleLink.dataset.analyticsLinkType = "direct";
  requiredElement<HTMLElement>("#external-map-links").hidden = !hasExternalLink;
  requiredElement<HTMLElement>("#crowd-feedback").hidden =
    !isCustomAnalyticsEnabled() || cafe.level === null || cafe.freshness === "stale";
  requiredElement<HTMLElement>("#cafe-panel").hidden = false;
  document.body.classList.add("panel-open");
}

export function showCafePanel(cafe: CafeProperties): void {
  if (openCafeId !== cafe.id) resetCrowdFeedback();
  openCafeId = cafe.id;
  openCafeDetail = cafe;
  renderCafePanel(cafe);
}

export function showCafePanelLoading(cafe: CafeMapProperties): void {
  openCafeId = cafe.id;
  openCafeDetail = null;
  resetCrowdFeedback();
  requiredElement<HTMLElement>("#cafe-address").textContent = "상세 정보 불러오는 중";
  requiredElement<HTMLElement>("#cafe-phone").textContent = "";
  const website = requiredElement<HTMLAnchorElement>("#cafe-website");
  website.hidden = true;
  website.removeAttribute("href");
  requiredElement<HTMLElement>("#cafe-source").textContent = "장소 정보를 확인하고 있습니다.";
  resetExternalLinks();
  showPanelShell(cafe);
  requiredElement<HTMLElement>("#cafe-evidence").textContent =
    "상세 근거 불러오는 중";
}

export function showCafePanelError(
  cafe: CafeMapProperties,
  message: string,
): void {
  if (openCafeId !== cafe.id) return;
  openCafeDetail = null;
  resetCrowdFeedback();
  requiredElement<HTMLElement>("#cafe-address").textContent = message;
  requiredElement<HTMLElement>("#cafe-phone").textContent = "잠시 후 다시 선택해 주세요";
  requiredElement<HTMLElement>("#cafe-source").textContent = "지도 혼잡도는 계속 볼 수 있습니다.";
  resetExternalLinks();
  showPanelShell(cafe);
  requiredElement<HTMLElement>("#cafe-evidence").textContent =
    "상세 근거를 불러오지 못했습니다.";
}

export function updateOpenCafePanel(cafe: CafeMapProperties): void {
  if (openCafeId !== cafe.id) return;
  if (openCafeDetail !== null) {
    openCafeDetail = { ...openCafeDetail, ...cafe };
    renderCafePanel(openCafeDetail);
    return;
  }
  renderCrowdEstimate(cafe, null);
}

export function getOpenCafeId(): string | null {
  return openCafeId;
}

export function hideCafePanel(): void {
  openCafeId = null;
  openCafeDetail = null;
  requiredElement<HTMLElement>("#cafe-panel").hidden = true;
  document.body.classList.remove("panel-open");
}
