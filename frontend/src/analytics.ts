import {
  inject,
  track,
  type BeforeSendEvent,
} from "@vercel/analytics";

export type ExternalMapProvider = "naver" | "kakao" | "google";
export type ExternalMapLinkType = "direct" | "search";
export type GeolocationResult = "success" | "error";
export type CrowdFeedback = "similar" | "busier" | "quieter";
export type CafeCoverage = "covered" | "fringe" | "uncovered";
export type CafeSearchMode = "text" | "brand" | "both";
export type CafeSearchResultBucket = "0" | "1-5" | "6-20";
export type CafeBrand =
  | "스타벅스"
  | "투썸플레이스"
  | "메가MGC커피"
  | "컴포즈커피"
  | "빽다방"
  | "이디야커피"
  | "폴바셋"
  | "더벤티"
  | "매머드커피"
  | "텐퍼센트커피"
  | "할리스"
  | "탐앤탐스"
  | "카페베네"
  | "커피빈"
  | "엔제리너스";

const CAFE_BRANDS = new Set<string>([
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
]);

export function stripAnalyticsUrlDetails(event: BeforeSendEvent): BeforeSendEvent | null {
  try {
    const url = new URL(event.url);
    url.search = "";
    url.hash = "";
    return { ...event, url: url.toString() };
  } catch {
    return null;
  }
}

export function initializeProductAnalytics(): void {
  inject({
    framework: "vite",
    mode: "auto",
    debug: false,
    beforeSend: stripAnalyticsUrlDetails,
  });
}

export function isCustomAnalyticsEnabled(): boolean {
  return import.meta.env.VITE_ENABLE_CUSTOM_ANALYTICS === "true";
}

function send(
  name: string,
  properties?: Record<string, string | number | boolean | null>,
): void {
  if (!isCustomAnalyticsEnabled()) return;
  try {
    track(name, properties);
  } catch {
    // Analytics must never block the map when the script is unavailable.
  }
}

export function trackMapReady(): void {
  send("map_ready");
}

export function trackCafeMarkerClick(
  coverage: CafeCoverage,
  colored: boolean,
): void {
  send("cafe_marker_click", { coverage, colored });
}

export function trackExternalMapClick(
  provider: ExternalMapProvider,
  linkType: ExternalMapLinkType,
): void {
  send("external_map_click", { provider, link_type: linkType });
}

export function trackGeolocationClick(): void {
  send("geolocation_click");
}

export function trackGeolocationResult(result: GeolocationResult): void {
  send("geolocation_result", { result });
}

export function trackViewportLoad(count: number, colored: number): void {
  const safeCount = Math.max(0, Math.floor(Number.isFinite(count) ? count : 0));
  const safeColored = Math.min(
    safeCount,
    Math.max(0, Math.floor(Number.isFinite(colored) ? colored : 0)),
  );
  send("viewport_load", { count: safeCount, colored: safeColored });
}

export function trackCafeDetailError(): void {
  send("cafe_detail_error");
}

export function isAnalyticsCafeBrand(brand: string): brand is CafeBrand {
  return CAFE_BRANDS.has(brand);
}

export function trackCafeSearchResult(
  count: number,
  mode: CafeSearchMode,
): void {
  const safeCount = Math.max(0, Math.floor(Number.isFinite(count) ? count : 0));
  const resultBucket: CafeSearchResultBucket = safeCount === 0
    ? "0"
    : safeCount <= 5
      ? "1-5"
      : "6-20";
  send("cafe_search_result", { result_bucket: resultBucket, mode });
}

export function trackCafeSearchSelect(mode: CafeSearchMode): void {
  send("cafe_search_select", { mode });
}

export function trackBrandFilter(
  brand: CafeBrand,
  state: "on" | "off",
): void {
  send("brand_filter", { brand, state });
}

export function trackCrowdFeedback(
  feedback: CrowdFeedback,
  predictedLevel: 1 | 2 | 3 | 4 | null,
  coverage: CafeCoverage,
): void {
  send("crowd_feedback", {
    feedback,
    context: `${coverage}:${predictedLevel ?? 0}`,
  });
}
