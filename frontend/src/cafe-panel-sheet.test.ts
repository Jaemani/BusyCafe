// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import {
  collapseCafePanelSheet,
  initializeCafePanelSheet,
} from "./cafe-panel-sheet";

describe("cafe panel sheet", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <aside id="cafe-panel">
        <button
          id="cafe-panel-sheet-toggle"
          type="button"
          aria-controls="cafe-panel-expanded-content"
          aria-expanded="false"
        >상세</button>
        <div id="cafe-panel-expanded-content">전체 내용</div>
      </aside>`;
  });

  it("starts compact and toggles accessible expanded state", () => {
    const panel = document.querySelector<HTMLElement>("#cafe-panel")!;
    const toggle = document.querySelector<HTMLButtonElement>(
      "#cafe-panel-sheet-toggle",
    )!;
    const controller = initializeCafePanelSheet(panel, toggle);

    expect(panel.dataset.sheetState).toBe("compact");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-label")).toBe("카페 상세 펼치기");

    toggle.click();
    expect(panel.dataset.sheetState).toBe("expanded");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-label")).toBe("카페 상세 접기");

    toggle.click();
    expect(panel.dataset.sheetState).toBe("compact");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    controller.destroy();
  });

  it("collapses from Escape and the selection reset hook", () => {
    const panel = document.querySelector<HTMLElement>("#cafe-panel")!;
    const toggle = document.querySelector<HTMLButtonElement>(
      "#cafe-panel-sheet-toggle",
    )!;
    const controller = initializeCafePanelSheet(panel, toggle);

    controller.expand();
    panel.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(panel.dataset.sheetState).toBe("compact");
    expect(document.activeElement).toBe(toggle);

    controller.expand();
    panel.scrollTop = 120;
    collapseCafePanelSheet();
    expect(panel.dataset.sheetState).toBe("compact");
    expect(panel.scrollTop).toBe(0);
    controller.destroy();
  });
});
