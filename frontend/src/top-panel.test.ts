// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import {
  initializeTopPanel,
  TOP_PANEL_STORAGE_KEY,
} from "./top-panel";

function elements() {
  return {
    shell: document.querySelector<HTMLElement>("#map-top-shell")!,
    header: document.querySelector<HTMLElement>("#map-header")!,
    collapse: document.querySelector<HTMLButtonElement>("#top-panel-collapse")!,
    expand: document.querySelector<HTMLButtonElement>("#top-panel-expand")!,
  };
}

describe("top panel", () => {
  beforeEach(() => {
    localStorage.clear();
    document.body.innerHTML = `
      <div id="map-top-shell">
        <header id="map-header">
          <button id="top-panel-collapse" aria-controls="map-header"></button>
        </header>
        <button id="top-panel-expand" aria-controls="map-header" hidden></button>
      </div>`;
  });

  it("starts expanded and persists both toggle directions", () => {
    const { shell, header, collapse, expand } = elements();
    const controller = initializeTopPanel(shell, header, collapse, expand);

    expect(shell.dataset.collapsed).toBe("false");
    expect(header.hidden).toBe(false);
    expect(expand.hidden).toBe(true);
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    expect(collapse.getAttribute("aria-label")).toBe("상단 검색 패널 접기");

    collapse.click();
    expect(shell.dataset.collapsed).toBe("true");
    expect(header.hidden).toBe(true);
    expect(expand.hidden).toBe(false);
    expect(collapse.getAttribute("aria-expanded")).toBe("false");
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    expect(expand.getAttribute("aria-label")).toBe("상단 검색 패널 펼치기");
    expect(localStorage.getItem(TOP_PANEL_STORAGE_KEY)).toBe("true");

    expand.click();
    expect(shell.dataset.collapsed).toBe("false");
    expect(header.hidden).toBe(false);
    expect(expand.hidden).toBe(true);
    expect(expand.getAttribute("aria-expanded")).toBe("true");
    expect(localStorage.getItem(TOP_PANEL_STORAGE_KEY)).toBe("false");
    controller.destroy();
  });

  it("restores a collapsed panel from local storage", () => {
    localStorage.setItem(TOP_PANEL_STORAGE_KEY, "true");
    const { shell, header, collapse, expand } = elements();
    initializeTopPanel(shell, header, collapse, expand);

    expect(shell.dataset.collapsed).toBe("true");
    expect(header.hidden).toBe(true);
    expect(expand.hidden).toBe(false);
    expect(expand.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps working when storage access fails and removes listeners on destroy", () => {
    const { shell, header, collapse, expand } = elements();
    const storage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const controller = initializeTopPanel(
      shell,
      header,
      collapse,
      expand,
      storage,
    );

    collapse.click();
    expect(shell.dataset.collapsed).toBe("true");
    controller.destroy();
    expand.click();
    expect(header.hidden).toBe(true);
  });
});
