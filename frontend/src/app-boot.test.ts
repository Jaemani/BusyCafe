// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// @ts-expect-error The app intentionally does not ship Node types; Vitest runs in Node.
import { readFileSync } from "node:fs";
import {
  APP_BOOT_STYLE_ID,
  APP_CSS_READY_PROPERTY,
  APP_LOAD_FAILED_CLASS,
  APP_LOADING_CLASS,
  hasReadyAppStyles,
  initializeAppBoot,
} from "./app-boot";

const indexHtml = readFileSync("index.html", "utf8");
const appStyles = readFileSync("src/style.css", "utf8");
const vercelConfig = JSON.parse(
  readFileSync("../vercel.json", "utf8"),
) as {
  headers: Array<{
    source: string;
    headers: Array<{ key: string; value: string }>;
  }>;
};

describe("app boot gate", () => {
  beforeEach(() => {
    document.head.querySelectorAll("style, link[rel~='stylesheet']").forEach((node) => node.remove());
    document.body.innerHTML = `
      <div id="app-boot-overlay">
        <button id="app-boot-retry" type="button">다시 시도</button>
      </div>
      <main id="app"></main>`;
    document.body.className = APP_LOADING_CLASS;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("ignores the critical boot style and reveals when app CSS is ready", () => {
    const bootStyle = document.createElement("style");
    bootStyle.id = APP_BOOT_STYLE_ID;
    bootStyle.textContent = `body.${APP_LOADING_CLASS} { visibility: hidden; }`;
    document.head.append(bootStyle);
    expect(hasReadyAppStyles(document)).toBe(false);

    const appStyle = document.createElement("style");
    appStyle.textContent = `:root { ${APP_CSS_READY_PROPERTY}: 1; }`;
    document.head.append(appStyle);
    expect(hasReadyAppStyles(document)).toBe(true);

    const controller = initializeAppBoot({
      isDocumentLoaded: () => false,
    });
    expect(controller.revealed).toBe(true);
    expect(controller.reason).toBe("css");
    expect(document.body.classList.contains(APP_LOADING_CLASS)).toBe(false);
  });

  it("shows retry UI on window load without revealing raw markup", () => {
    const controller = initializeAppBoot({
      isCssReady: () => false,
      isDocumentLoaded: () => false,
    });

    expect(controller.revealed).toBe(false);
    window.dispatchEvent(new Event("load"));
    expect(controller.revealed).toBe(false);
    expect(controller.failed).toBe(true);
    expect(controller.reason).toBe("load");
    expect(document.body.classList.contains(APP_LOADING_CLASS)).toBe(true);
    expect(document.body.classList.contains(APP_LOAD_FAILED_CLASS)).toBe(true);
  });

  it("checks CSS again when a stylesheet link finishes loading", () => {
    let cssReady = false;
    const controller = initializeAppBoot({
      isCssReady: () => cssReady,
      isDocumentLoaded: () => false,
    });
    const link = document.createElement("link");
    link.rel = "stylesheet";
    document.head.append(link);

    cssReady = true;
    link.dispatchEvent(new Event("load"));
    expect(controller.revealed).toBe(true);
    expect(controller.failed).toBe(false);
    expect(controller.reason).toBe("css");
    expect(document.body.classList.contains(APP_LOADING_CLASS)).toBe(false);
    expect(document.body.classList.contains(APP_LOAD_FAILED_CLASS)).toBe(false);
  });

  it("shows retry UI on timeout without revealing raw markup", () => {
    vi.useFakeTimers();
    const controller = initializeAppBoot({
      isCssReady: () => false,
      isDocumentLoaded: () => false,
      timeoutMs: 250,
    });

    vi.advanceTimersByTime(249);
    expect(controller.revealed).toBe(false);
    vi.advanceTimersByTime(1);
    expect(controller.revealed).toBe(false);
    expect(controller.failed).toBe(true);
    expect(controller.reason).toBe("timeout");
    expect(document.body.classList.contains(APP_LOADING_CLASS)).toBe(true);
    expect(document.body.classList.contains(APP_LOAD_FAILED_CLASS)).toBe(true);

    window.dispatchEvent(new Event("load"));
    expect(controller.reason).toBe("timeout");
  });

  it("reloads when the failed boot retry button is pressed", () => {
    const reload = vi.fn();
    const controller = initializeAppBoot({
      isCssReady: () => false,
      isDocumentLoaded: () => true,
      reload,
    });

    expect(controller.failed).toBe(true);
    document.querySelector<HTMLButtonElement>("#app-boot-retry")!.click();
    expect(reload).toHaveBeenCalledOnce();
  });

  it("destroy cancels fallback work without changing current visibility", () => {
    vi.useFakeTimers();
    const controller = initializeAppBoot({
      isCssReady: () => false,
      isDocumentLoaded: () => false,
      timeoutMs: 250,
    });

    controller.destroy();
    window.dispatchEvent(new Event("load"));
    vi.advanceTimersByTime(250);
    expect(controller.revealed).toBe(false);
    expect(controller.reason).toBeNull();
    expect(document.body.classList.contains(APP_LOADING_CLASS)).toBe(true);
  });

  it("ships the critical HTML gate, CSS marker, and immutable hashed assets", () => {
    expect(indexHtml).toContain(`<style id="${APP_BOOT_STYLE_ID}">`);
    expect(indexHtml).toContain(`<body class="${APP_LOADING_CLASS}">`);
    expect(indexHtml).toContain(`body.${APP_LOADING_CLASS} #app { visibility: hidden; }`);
    expect(indexHtml).toContain('id="app-boot-overlay"');
    expect(indexHtml).toContain('id="app-boot-retry"');
    expect(indexHtml).toContain("<noscript>");
    expect(appStyles).toContain(`${APP_CSS_READY_PROPERTY}: 1;`);

    const assets = vercelConfig.headers.find((entry) => entry.source === "/assets/(.*)");
    expect(assets?.headers).toContainEqual({
      key: "Cache-Control",
      value: "public, max-age=31536000, s-maxage=31536000, immutable",
    });
  });
});
