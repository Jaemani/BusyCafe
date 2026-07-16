import { describe, expect, it } from "vitest";

// @ts-expect-error The app intentionally does not ship Node types; Vitest runs in Node.
import { readFileSync } from "node:fs";

const styles = readFileSync(new URL("./style.css", import.meta.url), "utf8");

describe("responsive map layout", () => {
  it("does not force the document wider than a narrow device viewport", () => {
    expect(styles).not.toContain("min-width: 20rem");
    expect(styles).not.toContain("100vw");
    expect(styles).toMatch(/body\s*{[\s\S]*?min-width:\s*0;/);
  });

  it("bounds the base cafe panel in short landscape viewports", () => {
    expect(styles).toMatch(/\.cafe-panel\s*{[\s\S]*?max-height:\s*calc\([\s\S]*?100dvh/);
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
});
