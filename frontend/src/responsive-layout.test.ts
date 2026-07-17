import { describe, expect, it } from "vitest";

// @ts-expect-error The app intentionally does not ship Node types; Vitest runs in Node.
import { readFileSync } from "node:fs";

const styles = readFileSync(new URL("./style.css", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const mapSource = readFileSync(new URL("./map.ts", import.meta.url), "utf8");
const manifest = readFileSync(
  new URL("../public/manifest.webmanifest", import.meta.url),
  "utf8",
);

describe("responsive map layout", () => {
  it("does not force the document wider than a narrow device viewport", () => {
    expect(styles).not.toContain("min-width: 20rem");
    expect(styles).not.toContain("100vw");
    expect(styles).toMatch(/body\s*{[\s\S]*?min-width:\s*0;/);
    expect(styles).toMatch(/-webkit-text-size-adjust:\s*100%;/);
    expect(styles).toMatch(/text-size-adjust:\s*100%;/);
  });

  it("prevents iOS Safari from zooming focused search fields", () => {
    expect(styles).toMatch(
      /@media \(max-width: 40rem\)[\s\S]*?\.cafe-search-form input,[\s\S]*?\.missing-cafe-report input\s*{\s*font-size:\s*16px;/,
    );
  });

  it("keeps the map on the large viewport and binds overlays to VisualViewport", () => {
    expect(styles).toMatch(
      /#app\s*{[\s\S]*?position:\s*fixed;[\s\S]*?inset:\s*0;[\s\S]*?height:\s*100lvh;/,
    );
    expect(styles).toMatch(
      /\.viewport-ui\s*{[\s\S]*?top:\s*var\(--app-viewport-offset-top, 0px\);[\s\S]*?left:\s*var\(--app-viewport-offset-left, 0px\);/,
    );
    expect(styles).toMatch(/\.viewport-ui\s*{[\s\S]*?width:\s*var\(--app-viewport-width, 100%\);/);
    expect(styles).toMatch(/\.viewport-ui\s*{[\s\S]*?height:\s*var\(--app-viewport-height, 100%\);/);
  });

  it("keeps the map full-bleed and overlays in the live UI coordinate system", () => {
    expect(styles).toMatch(/#map\s*{[\s\S]*?position:\s*absolute;[\s\S]*?inset:\s*0;/);
    expect(indexHtml).toMatch(/<div id="map"[\s\S]*?<div id="viewport-ui"/);
    expect(styles).toMatch(/\.map-top-shell\s*{[\s\S]*?position:\s*absolute;/);
    expect(styles).toMatch(/\.cafe-panel\s*{[\s\S]*?position:\s*absolute;/);
    expect(styles).toMatch(/\.legend\s*{[\s\S]*?position:\s*absolute;/);
    expect(styles).not.toMatch(/\.maplibregl-ctrl-attrib\.maplibregl-compact\s*{[\s\S]*?position:\s*fixed;/);
  });

  it("lets map gestures pass through the UI container outside real overlays", () => {
    expect(styles).toMatch(/\.viewport-ui\s*{[\s\S]*?pointer-events:\s*none;/);
    expect(styles).toMatch(/\.viewport-ui\s*>\s*\*\s*{\s*pointer-events:\s*auto;/);
  });

  it("prevents grid min-content from widening the top shell", () => {
    expect(styles).toMatch(
      /\.map-top-shell\s*{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\);/,
    );
    expect(styles).toMatch(
      /\.map-header\s*{[\s\S]*?max-width:\s*100%;[\s\S]*?min-width:\s*0;/,
    );
  });

  it("collapses the top panel to a logo attached to the left edge", () => {
    const expandButton = indexHtml.match(
      /<button[^>]*id="top-panel-expand"[\s\S]*?<\/button>/,
    )?.[0];

    expect(indexHtml).toContain('id="top-panel-collapse"');
    expect(expandButton).toContain('src="/logo.png"');
    expect(expandButton).not.toContain("BUSY CAFE");
    expect(styles).toMatch(
      /\.map-top-shell\[data-collapsed="true"\]\s*{[\s\S]*?right:\s*auto;[\s\S]*?left:\s*0;/,
    );
    expect(styles).toMatch(
      /\.map-top-shell\[data-collapsed="true"\] \.top-panel-expand\s*{[\s\S]*?display:\s*grid;/,
    );
  });

  it("keeps brand chips hidden behind a control beside the collapse button", () => {
    expect(indexHtml).toMatch(
      /id="cafe-brand-filters"[^>]*hidden/,
    );
    expect(indexHtml).toMatch(
      /id="brand-filter-toggle"[\s\S]*?aria-controls="cafe-brand-filters"/,
    );
    expect(styles).toMatch(
      /\.brand-filter-toggle\s*{[\s\S]*?right:\s*2\.12rem;[\s\S]*?bottom:\s*0\.32rem;/,
    );
    expect(styles).toMatch(
      /\.cafe-brand-filters\s*{[\s\S]*?padding:\s*0\.04rem 0\.04rem 0\.2rem;/,
    );
    expect(styles).toMatch(/\.cafe-brand-filters\[hidden\]\s*{\s*display:\s*none;/);
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

  it("keeps service and privacy links in the header footer only", () => {
    const headerMeta = indexHtml.match(
      /<div class="map-header-meta">([\s\S]*?)<\/div>/,
    )?.[1];
    const sourceLine = indexHtml.match(
      /<p class="source-line">([\s\S]*?)<\/p>/,
    )?.[1];

    expect(headerMeta).not.toContain("서비스 안내");
    expect(headerMeta).not.toContain("개인정보");
    expect(sourceLine).toContain("서비스·데이터 안내");
    expect(sourceLine).toContain("개인정보");
  });

  it("supports full safe-area use when launched from the iOS home screen", () => {
    expect(indexHtml).toContain('name="apple-mobile-web-app-capable" content="yes"');
    expect(indexHtml).toContain(
      'name="apple-mobile-web-app-status-bar-style" content="black-translucent"',
    );
    expect(indexHtml).toContain('rel="manifest" href="/manifest.webmanifest"');
  });

  it("matches Safari chrome and install metadata to the map canvas background", () => {
    expect(styles).toContain("--map-canvas-background: #f2f3f0");
    expect(styles).toMatch(
      /html,\s*body\s*{[\s\S]*?background:\s*var\(--map-canvas-background\);/,
    );
    expect(indexHtml).toContain('name="theme-color" content="#f2f3f0"');
    expect(manifest).toContain('"background_color": "#f2f3f0"');
    expect(manifest).toContain('"theme_color": "#f2f3f0"');
  });

  it("starts attribution collapsed at the bottom-left", () => {
    expect(mapSource).toContain('map.addControl(attributionControl, "bottom-left")');
    expect(mapSource).toContain('classList.remove("maplibregl-compact-show")');
    expect(mapSource).toContain('classList.add("busy-attribution-collapsed")');
    expect(styles).toMatch(
      /\.maplibregl-ctrl-attrib\.busy-attribution-collapsed[\s\S]*?width:\s*24px/,
    );
    expect(styles).toMatch(
      /\.maplibregl-ctrl-bottom-left\s*{[\s\S]*?bottom:[\s\S]*?left:/,
    );
  });

  it("keeps mobile controls above the lowered legend", () => {
    expect(styles).toMatch(
      /@media \(max-width: 40rem\)[\s\S]*?\.legend\s*{[\s\S]*?bottom:\s*max\(1\.8rem,[\s\S]*?safe-area-inset-bottom[\s\S]*?1\.6rem/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 40rem\)[\s\S]*?\.maplibregl-ctrl-bottom-right\s*{[\s\S]*?var\(--app-viewport-height, 100%\)[\s\S]*?max\(6\.5rem/,
    );
  });

  it("uses restrained cafe and cluster outlines", () => {
    expect(mapSource).toContain('"circle-stroke-width": 1.5');
    expect(mapSource).not.toContain('"circle-stroke-width": 3');
    expect(mapSource).not.toMatch(/"fringe",\s*4,/);
  });
});
