import { describe, expect, it } from "vitest";

// @ts-expect-error The app intentionally does not ship Node types; Vitest runs in Node.
import { readFileSync } from "node:fs";

const styles = readFileSync(new URL("./style.css", import.meta.url), "utf8");

describe("responsive map layout", () => {
  it("does not force the document wider than a narrow device viewport", () => {
    expect(styles).not.toContain("min-width: 20rem");
    expect(styles).not.toContain("100vw");
    expect(styles).toMatch(/body\s*{[\s\S]*?min-width:\s*0;/);
    expect(styles).toMatch(/-webkit-text-size-adjust:\s*100%;/);
    expect(styles).toMatch(/text-size-adjust:\s*100%;/);
  });

  it("binds the app to VisualViewport dimensions with safe fallbacks", () => {
    expect(styles).toMatch(
      /#app\s*{[\s\S]*?position:\s*fixed;[\s\S]*?top:\s*var\(--app-viewport-offset-top, 0px\);[\s\S]*?left:\s*var\(--app-viewport-offset-left, 0px\);/,
    );
    expect(styles).toMatch(/width:\s*var\(--app-viewport-width, 100%\);/);
    expect(styles).toMatch(/height:\s*var\(--app-viewport-height, 100dvh\);/);
  });

  it("keeps the map and overlays in the app coordinate system", () => {
    expect(styles).toMatch(/#map\s*{[\s\S]*?position:\s*absolute;[\s\S]*?inset:\s*0;/);
    expect(styles).toMatch(/\.map-top-shell\s*{[\s\S]*?position:\s*absolute;/);
    expect(styles).toMatch(/\.cafe-panel\s*{[\s\S]*?position:\s*absolute;/);
    expect(styles).toMatch(/\.legend\s*{[\s\S]*?position:\s*absolute;/);
    expect(styles).not.toMatch(/\.maplibregl-ctrl-attrib\.maplibregl-compact\s*{[\s\S]*?position:\s*fixed;/);
  });

  it("prevents grid min-content from widening the top shell", () => {
    expect(styles).toMatch(
      /\.map-top-shell\s*{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\);/,
    );
    expect(styles).toMatch(
      /\.map-header\s*{[\s\S]*?max-width:\s*100%;[\s\S]*?min-width:\s*0;/,
    );
  });

  it("bounds the base cafe panel to its app container in short landscapes", () => {
    expect(styles).toMatch(/\.cafe-panel\s*{[\s\S]*?max-height:\s*calc\([\s\S]*?100%/);
    expect(styles).toMatch(/\.cafe-panel\s*{[\s\S]*?overflow-x:\s*hidden;/);
    expect(styles).toMatch(/\.cafe-panel\s*{[\s\S]*?overflow-y:\s*auto;/);
  });

  it("lets the mobile sheet fit between both horizontal viewport edges", () => {
    expect(styles).toMatch(
      /@media \(max-width: 40rem\)[\s\S]*?\.cafe-panel\s*{[\s\S]*?left:\s*0;[\s\S]*?width:\s*auto;[\s\S]*?max-width:\s*none;/,
    );
  });

  it("uses the larger of base spacing and each safe-area inset", () => {
    expect(styles).toMatch(
      /\.map-top-shell\s*{[\s\S]*?top:\s*max\(1\.1rem, env\(safe-area-inset-top, 0px\)\);[\s\S]*?right:\s*max\(1\.1rem, env\(safe-area-inset-right, 0px\)\);[\s\S]*?left:\s*max\(1\.1rem, env\(safe-area-inset-left, 0px\)\);/,
    );
    expect(styles).toMatch(
      /padding:[\s\S]*?max\(1\.2rem, env\(safe-area-inset-right, 0px\)\)[\s\S]*?max\(1\.4rem, env\(safe-area-inset-bottom, 0px\)\)[\s\S]*?max\(1\.2rem, env\(safe-area-inset-left, 0px\)\)/,
    );
  });

  it("sizes compact and expanded sheets from the live app height", () => {
    expect(styles).toMatch(
      /@media \(max-width: 40rem\)[\s\S]*?\.cafe-panel\s*{[\s\S]*?max-height:\s*min\([\s\S]*?54%/,
    );
    expect(styles).toMatch(
      /\.cafe-panel\[data-sheet-state="expanded"\]\s*{[\s\S]*?max-height:\s*min\([\s\S]*?90%/,
    );
  });
});
