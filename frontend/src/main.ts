import "./style.css";
import { initializeCafeMap } from "./map";
import { hideCafePanel } from "./panel";

const status = document.querySelector<HTMLElement>("#search-status");
const closeButton = document.querySelector<HTMLButtonElement>("#panel-close");

if (!status || !closeButton) {
  throw new Error("Required map UI elements were not found");
}

closeButton.addEventListener("click", hideCafePanel);

initializeCafeMap(status).catch((error: unknown) => {
  const message = error instanceof Error ? error.message : "지도를 불러오지 못했습니다";
  status.textContent = message;
  status.dataset.state = "error";
});
