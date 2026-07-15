import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import {
  initializeProductAnalytics,
  trackExternalMapClick,
  trackMapReady,
  type ExternalMapLinkType,
  type ExternalMapProvider,
} from "./analytics";
import { initializeCafeMap } from "./map";
import { hideCafePanel, initializeCrowdFeedback } from "./panel";
import { initializeCommuteNotice } from "./commute-notice";

const status = document.querySelector<HTMLElement>("#search-status");
const closeButton = document.querySelector<HTMLButtonElement>("#panel-close");

if (!status || !closeButton) {
  throw new Error("Required map UI elements were not found");
}

closeButton.addEventListener("click", hideCafePanel);
initializeProductAnalytics();
initializeCrowdFeedback();
initializeCommuteNotice();

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const link = target.closest<HTMLAnchorElement>("[data-analytics-provider]");
  if (!link || !link.href) return;
  const provider = link.dataset.analyticsProvider;
  const linkType = link.dataset.analyticsLinkType;
  if (
    (provider === "naver" || provider === "kakao" || provider === "google") &&
    (linkType === "direct" || linkType === "search")
  ) {
    trackExternalMapClick(
      provider as ExternalMapProvider,
      linkType as ExternalMapLinkType,
    );
  }
});

initializeCafeMap(status)
  .then(trackMapReady)
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "지도를 불러오지 못했습니다";
    status.textContent = message;
    status.dataset.state = "error";
  });
