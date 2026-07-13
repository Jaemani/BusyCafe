import type { CafeProperties } from "./cafe-provider";

function requiredElement<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing UI element: ${selector}`);
  return element;
}

function formatEvidence(cafe: CafeProperties): string {
  if (!cafe.hotspotName || cafe.distanceM === null) {
    return "이 지역은 아직 혼잡도 근거가 연결되지 않았어요.";
  }
  return `${cafe.hotspotName} 기준 · ${Math.round(cafe.distanceM).toLocaleString("ko-KR")}m`;
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

function setExternalLink(selector: string, href: string | null): boolean {
  const link = requiredElement<HTMLAnchorElement>(selector);
  if (!href) {
    link.hidden = true;
    link.removeAttribute("href");
    return false;
  }
  link.href = href;
  link.hidden = false;
  return true;
}

export function showCafePanel(cafe: CafeProperties): void {
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
      : cafe.confidenceTier === null
        ? "근거 강도 산정 전"
        : `근거 강도 ${EVIDENCE_STRENGTH_LABELS[cafe.confidenceTier]}`;
  requiredElement<HTMLElement>("#estimate-dot").dataset.level = String(cafe.level ?? 0);
  requiredElement<HTMLElement>("#cafe-evidence").textContent = formatEvidence(cafe);
  requiredElement<HTMLElement>("#cafe-source").textContent =
    `${cafe.sourceLabel} · 원본과 검증 상태를 함께 표시합니다.`;
  const hasExternalLink = [
    setExternalLink("#map-link-naver", cafe.naverUrl),
    setExternalLink("#map-link-kakao", cafe.kakaoUrl),
    setExternalLink("#map-link-google", cafe.googleUrl),
  ].some(Boolean);
  requiredElement<HTMLElement>("#external-map-links").hidden = !hasExternalLink;
  requiredElement<HTMLElement>("#cafe-panel").hidden = false;
  document.body.classList.add("panel-open");
}

export function hideCafePanel(): void {
  requiredElement<HTMLElement>("#cafe-panel").hidden = true;
  document.body.classList.remove("panel-open");
}
