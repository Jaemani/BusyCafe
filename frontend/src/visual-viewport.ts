export interface AppViewportSnapshot {
  width: number;
  height: number;
  offsetTop: number;
  offsetLeft: number;
}

type ViewportListener = (snapshot: AppViewportSnapshot) => void;

interface EventSource {
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
}

interface VisualViewportSource extends EventSource {
  width: number;
  height: number;
  offsetTop: number;
  offsetLeft: number;
}

interface ViewportWindow extends EventSource {
  readonly visualViewport?: VisualViewportSource | null;
  readonly innerWidth: number;
  readonly innerHeight: number;
  requestAnimationFrame(callback: FrameRequestCallback): number;
  cancelAnimationFrame(handle: number): void;
}

interface ViewportDocument extends EventSource {
  readonly hidden: boolean;
  readonly documentElement: {
    readonly style: Pick<CSSStyleDeclaration, "setProperty">;
  };
}

export interface AppViewportController {
  subscribe(listener: ViewportListener): () => void;
  destroy(): void;
}

const CSS_VARIABLES = {
  width: "--app-viewport-width",
  height: "--app-viewport-height",
  offsetTop: "--app-viewport-offset-top",
  offsetLeft: "--app-viewport-offset-left",
} as const;

function normalizedPixels(value: number, fallback: number): number {
  if (!Number.isFinite(value) || value < 0) return fallback;
  return Math.round(value * 100) / 100;
}

function readSnapshot(win: ViewportWindow): AppViewportSnapshot {
  const viewport = win.visualViewport;
  const fallbackWidth = normalizedPixels(win.innerWidth, 0);
  const fallbackHeight = normalizedPixels(win.innerHeight, 0);
  if (!viewport) {
    return {
      width: fallbackWidth,
      height: fallbackHeight,
      offsetTop: 0,
      offsetLeft: 0,
    };
  }
  return {
    width: normalizedPixels(viewport.width, fallbackWidth),
    height: normalizedPixels(viewport.height, fallbackHeight),
    offsetTop: normalizedPixels(viewport.offsetTop, 0),
    offsetLeft: normalizedPixels(viewport.offsetLeft, 0),
  };
}

function snapshotsEqual(
  left: AppViewportSnapshot | null,
  right: AppViewportSnapshot,
): boolean {
  return left !== null &&
    left.width === right.width &&
    left.height === right.height &&
    left.offsetTop === right.offsetTop &&
    left.offsetLeft === right.offsetLeft;
}

/**
 * Mirrors the browser's visual viewport into CSS variables. Safari and Chrome
 * can resize or offset that viewport while their browser chrome, keyboard, or
 * a restored tab changes without updating 100vh consistently.
 */
export function createAppViewportController(
  win: ViewportWindow = window,
  doc: ViewportDocument = document,
): AppViewportController {
  const listeners = new Set<ViewportListener>();
  let lastSnapshot: AppViewportSnapshot | null = null;
  let animationFrameId: number | null = null;
  let notifyOnNextFrame = false;
  let destroyed = false;

  const applySnapshot = (forceNotify = false): void => {
    if (destroyed) return;
    const snapshot = readSnapshot(win);
    const changed = !snapshotsEqual(lastSnapshot, snapshot);
    if (changed) {
      const style = doc.documentElement.style;
      style.setProperty(CSS_VARIABLES.width, `${snapshot.width}px`);
      style.setProperty(CSS_VARIABLES.height, `${snapshot.height}px`);
      style.setProperty(CSS_VARIABLES.offsetTop, `${snapshot.offsetTop}px`);
      style.setProperty(CSS_VARIABLES.offsetLeft, `${snapshot.offsetLeft}px`);
      lastSnapshot = snapshot;
    }
    if (changed || forceNotify) {
      for (const listener of listeners) listener(snapshot);
    }
  };

  const schedule = (forceNotify = false): void => {
    if (destroyed) return;
    notifyOnNextFrame ||= forceNotify;
    if (animationFrameId !== null) return;
    animationFrameId = win.requestAnimationFrame(() => {
      animationFrameId = null;
      const shouldNotify = notifyOnNextFrame;
      notifyOnNextFrame = false;
      applySnapshot(shouldNotify);
    });
  };

  const scheduleChange: EventListener = () => schedule();
  const scheduleResume: EventListener = () => schedule(true);
  const scheduleVisibleResume: EventListener = () => {
    if (!doc.hidden) schedule(true);
  };

  const visualViewport = win.visualViewport;
  visualViewport?.addEventListener("resize", scheduleChange);
  visualViewport?.addEventListener("scroll", scheduleChange);
  win.addEventListener("resize", scheduleChange);
  win.addEventListener("orientationchange", scheduleChange);
  win.addEventListener("pageshow", scheduleResume);
  doc.addEventListener("visibilitychange", scheduleVisibleResume);

  // Set dimensions before MapLibre's first layout. Later event bursts use one
  // animation frame so address-bar scroll does not repeatedly resize canvas.
  applySnapshot();

  return {
    subscribe(listener: ViewportListener): () => void {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      if (animationFrameId !== null) win.cancelAnimationFrame(animationFrameId);
      visualViewport?.removeEventListener("resize", scheduleChange);
      visualViewport?.removeEventListener("scroll", scheduleChange);
      win.removeEventListener("resize", scheduleChange);
      win.removeEventListener("orientationchange", scheduleChange);
      win.removeEventListener("pageshow", scheduleResume);
      doc.removeEventListener("visibilitychange", scheduleVisibleResume);
      listeners.clear();
    },
  };
}

let appViewportController: AppViewportController | null = null;

export function initializeAppViewport(): AppViewportController {
  appViewportController ??= createAppViewportController();
  return appViewportController;
}
