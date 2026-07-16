// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { initializeBrandFilterDisclosure } from "./brand-filter-disclosure";

describe("brand filter disclosure", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="cafe-brand-filters">브랜드 목록</div>
      <button
        id="brand-filter-toggle"
        aria-controls="cafe-brand-filters"
        aria-expanded="true"
      >브랜드</button>
      <button id="top-panel-collapse">상단 패널 접기</button>`;
  });

  function elements() {
    return {
      filters: document.querySelector<HTMLElement>("#cafe-brand-filters")!,
      toggle: document.querySelector<HTMLButtonElement>("#brand-filter-toggle")!,
      topCollapse: document.querySelector<HTMLButtonElement>("#top-panel-collapse")!,
    };
  }

  it("starts closed on every initialization and toggles accessible state", () => {
    const { filters, toggle, topCollapse } = elements();
    initializeBrandFilterDisclosure(filters, toggle, topCollapse);

    expect(filters.hidden).toBe(true);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe("cafe-brand-filters");
    expect(toggle.getAttribute("aria-label")).toBe("프랜차이즈 필터 열기");

    toggle.click();
    expect(filters.hidden).toBe(false);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-label")).toBe("프랜차이즈 필터 닫기");

    toggle.click();
    expect(filters.hidden).toBe(true);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes filters before the top panel is collapsed", () => {
    const { filters, toggle, topCollapse } = elements();
    initializeBrandFilterDisclosure(filters, toggle, topCollapse);

    toggle.click();
    expect(filters.hidden).toBe(false);
    topCollapse.click();
    expect(filters.hidden).toBe(true);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-label")).toBe("프랜차이즈 필터 열기");
  });

  it("removes both listeners on destroy", () => {
    const { filters, toggle, topCollapse } = elements();
    const controller = initializeBrandFilterDisclosure(
      filters,
      toggle,
      topCollapse,
    );

    controller.open();
    controller.destroy();
    topCollapse.click();
    expect(filters.hidden).toBe(false);
    toggle.click();
    expect(filters.hidden).toBe(false);
  });
});
