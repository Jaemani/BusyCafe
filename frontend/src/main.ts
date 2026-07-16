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
import { initializeCafePanelSheet } from "./cafe-panel-sheet";
import { initializeAppViewport } from "./visual-viewport";
import { initializeTopPanel } from "./top-panel";

initializeAppViewport();

const status = document.querySelector<HTMLElement>("#search-status");
const closeButton = document.querySelector<HTMLButtonElement>("#panel-close");
const cafePanel = document.querySelector<HTMLElement>("#cafe-panel");
const cafePanelSheetToggle = document.querySelector<HTMLButtonElement>(
  "#cafe-panel-sheet-toggle",
);
const mapTopShell = document.querySelector<HTMLElement>("#map-top-shell");
const mapHeader = document.querySelector<HTMLElement>("#map-header");
const topPanelCollapse = document.querySelector<HTMLButtonElement>(
  "#top-panel-collapse",
);
const topPanelExpand = document.querySelector<HTMLButtonElement>(
  "#top-panel-expand",
);

if (
  !status ||
  !closeButton ||
  !cafePanel ||
  !cafePanelSheetToggle ||
  !mapTopShell ||
  !mapHeader ||
  !topPanelCollapse ||
  !topPanelExpand
) {
  throw new Error("Required map UI elements were not found");
}

closeButton.addEventListener("click", hideCafePanel);
initializeCafePanelSheet(cafePanel, cafePanelSheetToggle);
initializeProductAnalytics();
initializeCrowdFeedback();
initializeCommuteNotice();
initializeTopPanel(
  mapTopShell,
  mapHeader,
  topPanelCollapse,
  topPanelExpand,
);

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
