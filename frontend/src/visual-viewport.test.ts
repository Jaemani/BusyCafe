import { describe, expect, it, vi } from "vitest";
import { createAppViewportController } from "./visual-viewport";

class FakeEventSource {
  private readonly listeners = new Map<string, Set<EventListener>>();

  addEventListener(type: string, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ type } as Event);
    }
  }
}

class FakeVisualViewport extends FakeEventSource {
  width = 390;
  height = 720;
  offsetTop = 47;
  offsetLeft = 0;
}

class FakeWindow extends FakeEventSource {
  visualViewport: FakeVisualViewport | null = new FakeVisualViewport();
  innerWidth = 430;
  innerHeight = 800;
  private nextFrameId = 1;
  private readonly frames = new Map<number, FrameRequestCallback>();

  requestAnimationFrame(callback: FrameRequestCallback): number {
    const id = this.nextFrameId++;
    this.frames.set(id, callback);
    return id;
  }

  cancelAnimationFrame(handle: number): void {
    this.frames.delete(handle);
  }

  pendingFrameCount(): number {
    return this.frames.size;
  }

  flushAnimationFrames(): void {
    const frames = [...this.frames.values()];
    this.frames.clear();
    for (const frame of frames) frame(0);
  }
}

class FakeDocument extends FakeEventSource {
  hidden = false;
  readonly setProperty = vi.fn<(name: string, value: string) => void>();
  readonly documentElement = { style: { setProperty: this.setProperty } };
}

describe("visual viewport controller", () => {
  it("publishes the initial visual viewport dimensions as CSS variables", () => {
    const win = new FakeWindow();
    const doc = new FakeDocument();
    const controller = createAppViewportController(win, doc);

    expect(doc.setProperty.mock.calls).toEqual([
      ["--app-viewport-width", "390px"],
      ["--app-viewport-height", "720px"],
      ["--app-viewport-offset-top", "47px"],
      ["--app-viewport-offset-left", "0px"],
    ]);

    controller.destroy();
  });

  it("coalesces resize and scroll bursts and notifies after CSS updates", () => {
    const win = new FakeWindow();
    const doc = new FakeDocument();
    const controller = createAppViewportController(win, doc);
    const listener = vi.fn();
    controller.subscribe(listener);
    const viewport = win.visualViewport!;

    viewport.height = 612.345;
    viewport.offsetTop = 52.126;
    viewport.dispatch("resize");
    viewport.dispatch("scroll");
    win.dispatch("resize");

    expect(win.pendingFrameCount()).toBe(1);
    expect(listener).not.toHaveBeenCalled();
    win.flushAnimationFrames();

    expect(doc.setProperty).toHaveBeenCalledWith("--app-viewport-height", "612.35px");
    expect(doc.setProperty).toHaveBeenCalledWith("--app-viewport-offset-top", "52.13px");
    expect(listener).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledWith({
      width: 390,
      height: 612.35,
      offsetTop: 52.13,
      offsetLeft: 0,
    });

    controller.destroy();
  });

  it("notifies on a visible page resume even when dimensions did not change", () => {
    const win = new FakeWindow();
    const doc = new FakeDocument();
    const controller = createAppViewportController(win, doc);
    const listener = vi.fn();
    controller.subscribe(listener);

    win.visualViewport!.dispatch("resize");
    win.flushAnimationFrames();
    expect(listener).not.toHaveBeenCalled();

    win.dispatch("pageshow");
    win.flushAnimationFrames();
    expect(listener).toHaveBeenCalledOnce();

    doc.hidden = true;
    doc.dispatch("visibilitychange");
    expect(win.pendingFrameCount()).toBe(0);
    doc.hidden = false;
    doc.dispatch("visibilitychange");
    win.flushAnimationFrames();
    expect(listener).toHaveBeenCalledTimes(2);

    controller.destroy();
  });

  it("falls back to the layout viewport and removes every listener on destroy", () => {
    const win = new FakeWindow();
    win.visualViewport = null;
    const doc = new FakeDocument();
    const controller = createAppViewportController(win, doc);
    const listener = vi.fn();
    controller.subscribe(listener);

    expect(doc.setProperty).toHaveBeenCalledWith("--app-viewport-width", "430px");
    expect(doc.setProperty).toHaveBeenCalledWith("--app-viewport-height", "800px");

    win.innerHeight = 640;
    win.dispatch("orientationchange");
    expect(win.pendingFrameCount()).toBe(1);
    controller.destroy();
    expect(win.pendingFrameCount()).toBe(0);

    win.dispatch("resize");
    win.dispatch("pageshow");
    doc.dispatch("visibilitychange");
    win.flushAnimationFrames();
    expect(listener).not.toHaveBeenCalled();
  });
});
