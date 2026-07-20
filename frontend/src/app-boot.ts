export const APP_LOADING_CLASS = "app-loading";
export const APP_LOAD_FAILED_CLASS = "app-load-failed";
export const APP_BOOT_STYLE_ID = "app-boot-style";
export const APP_CSS_READY_PROPERTY = "--busy-cafe-app-css-ready";
export const DEFAULT_APP_BOOT_TIMEOUT_MS = 4_000;

export type AppBootRevealReason = "css" | "load" | "timeout";

export interface AppBootController {
  readonly failed: boolean;
  readonly reason: AppBootRevealReason | null;
  readonly revealed: boolean;
  destroy(): void;
}

export interface AppBootOptions {
  document?: Document;
  isCssReady?: () => boolean;
  isDocumentLoaded?: () => boolean;
  timeoutMs?: number;
  window?: Window;
  reload?: () => void;
}

export function hasReadyAppStyles(
  documentRef: Document,
  windowRef: Window = window,
): boolean {
  return windowRef
    .getComputedStyle(documentRef.documentElement)
    .getPropertyValue(APP_CSS_READY_PROPERTY)
    .trim() === "1";
}

/**
 * Removes the initial boot gate only after application CSS is observable.
 * Window load and timeout switch the gate to a retry UI when CSS is missing.
 */
export function initializeAppBoot(
  options: AppBootOptions = {},
): AppBootController {
  const windowRef = options.window ?? window;
  const documentRef = options.document ?? document;
  const isCssReady = options.isCssReady ??
    (() => hasReadyAppStyles(documentRef, windowRef));
  const isDocumentLoaded = options.isDocumentLoaded ??
    (() => documentRef.readyState === "complete");
  const timeoutMs = options.timeoutMs ?? DEFAULT_APP_BOOT_TIMEOUT_MS;
  const retryButton = documentRef.querySelector<HTMLButtonElement>("#app-boot-retry");
  const reload = options.reload ?? (() => windowRef.location.reload());

  let reason: AppBootRevealReason | null = null;
  let destroyed = false;
  let timeoutId: number | null = null;

  const cleanup = (): void => {
    documentRef.removeEventListener("load", onResourceLoad, true);
    windowRef.removeEventListener("load", onWindowLoad);
    retryButton?.removeEventListener("click", onRetry);
    if (timeoutId !== null) {
      windowRef.clearTimeout(timeoutId);
      timeoutId = null;
    }
  };

  const reveal = (nextReason: AppBootRevealReason): void => {
    if (destroyed || reason === "css") return;
    reason = nextReason;
    documentRef.body.classList.remove(APP_LOADING_CLASS);
    documentRef.body.classList.remove(APP_LOAD_FAILED_CLASS);
    cleanup();
  };

  const fail = (nextReason: Exclude<AppBootRevealReason, "css">): void => {
    if (destroyed || reason !== null) return;
    reason = nextReason;
    documentRef.body.classList.add(APP_LOADING_CLASS, APP_LOAD_FAILED_CLASS);
    windowRef.removeEventListener("load", onWindowLoad);
    if (timeoutId !== null) {
      windowRef.clearTimeout(timeoutId);
      timeoutId = null;
    }
  };

  const revealIfCssReady = (): boolean => {
    if (!isCssReady()) return false;
    reveal("css");
    return true;
  };

  function onResourceLoad(event: Event): void {
    const target = event.target;
    if (!(target instanceof Element) || target.tagName !== "LINK") return;
    const rel = target.getAttribute("rel")?.split(/\s+/) ?? [];
    if (rel.includes("stylesheet")) revealIfCssReady();
  }

  function onWindowLoad(): void {
    if (!revealIfCssReady()) fail("load");
  }

  function onRetry(): void {
    reload();
  }

  documentRef.addEventListener("load", onResourceLoad, true);
  windowRef.addEventListener("load", onWindowLoad);
  retryButton?.addEventListener("click", onRetry);
  timeoutId = windowRef.setTimeout(() => {
    if (!revealIfCssReady()) fail("timeout");
  }, timeoutMs);

  if (!revealIfCssReady() && isDocumentLoaded()) fail("load");

  return {
    get failed() {
      return reason === "load" || reason === "timeout";
    },
    get reason() {
      return reason;
    },
    get revealed() {
      return reason === "css";
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      cleanup();
    },
  };
}
