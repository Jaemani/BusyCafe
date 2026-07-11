import type { KakaoCafe } from "./map";

function requiredElement<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing UI element: ${selector}`);
  }
  return element;
}

export function showCafePanel(cafe: KakaoCafe): void {
  const panel = requiredElement<HTMLElement>("#cafe-panel");
  const name = requiredElement<HTMLElement>("#cafe-name");
  const address = requiredElement<HTMLElement>("#cafe-address");
  const link = requiredElement<HTMLAnchorElement>("#cafe-link");

  name.textContent = cafe.place_name;
  address.textContent = cafe.road_address_name || cafe.address_name || "주소 정보 없음";
  link.href = cafe.place_url;
  panel.hidden = false;
}

export function hideCafePanel(): void {
  requiredElement<HTMLElement>("#cafe-panel").hidden = true;
}
