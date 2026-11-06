// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import indexHtml from "../index.html?raw";
import { dismissBootSplashAfterPaint, FADE_MS } from "./bootSplash";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => setTimeout(() => fn(0), 0));
  vi.spyOn(performance, "now").mockReturnValue(100);
  document.body.innerHTML = '<div id="boot-splash"></div>';
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

const done = () => document.getElementById("boot-splash")?.classList.contains("boot-splash--done") ?? true;

describe("boot splash", () => {
  it("on the phone panel goes as soon as the app has painted (the site shows its own)", async () => {
    window.history.replaceState(null, "", "/panel/index.html?direct=1");
    dismissBootSplashAfterPaint();
    await vi.advanceTimersByTimeAsync(5);
    expect(done()).toBe(true);
  });

  it("on the desktop goes as soon as the app has painted, with no extra hold", async () => {
    // The native splash already showed the logo; holding this one hid a drawn app ~1.2 s.
    dismissBootSplashAfterPaint();
    await vi.advanceTimersByTimeAsync(5);
    expect(done()).toBe(true);
  });

  it("is removed from the page once its fade ends", async () => {
    dismissBootSplashAfterPaint();
    await vi.advanceTimersByTimeAsync(5);
    expect(document.getElementById("boot-splash")).not.toBeNull();
    await vi.advanceTimersByTimeAsync(200);
    expect(document.getElementById("boot-splash")).toBeNull();
  });

  it("is gone within one frame plus the fade (<= 60 ms) of the root render", async () => {
    document.body.innerHTML = '<div id="root"><div>app</div></div><div id="boot-splash"></div>';
    dismissBootSplashAfterPaint();
    expect(FADE_MS).toBeLessThanOrEqual(60);
    await vi.advanceTimersByTimeAsync(1 + FADE_MS);
    expect(document.getElementById("boot-splash")).toBeNull();
  });

  it("is never removed while the root is still empty", async () => {
    document.body.innerHTML = '<div id="root"></div><div id="boot-splash"></div>';
    dismissBootSplashAfterPaint();
    await vi.advanceTimersByTimeAsync(500);
    expect(done()).toBe(false);
    document.getElementById("root")!.appendChild(document.createElement("div"));
    await vi.advanceTimersByTimeAsync(1 + FADE_MS);
    expect(document.getElementById("boot-splash")).toBeNull();
  });

  it("goes with no fade in the native launcher (its first data came from the start-up cache)", async () => {
    (window as Window & { pywebview?: unknown }).pywebview = { native: true };
    try {
      dismissBootSplashAfterPaint();
      await vi.advanceTimersByTimeAsync(1);
      expect(document.getElementById("boot-splash")).toBeNull();
    } finally {
      delete (window as Window & { pywebview?: unknown }).pywebview;
    }
  });

  it("goes with no fade when reduced motion is asked for", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce") }));
    dismissBootSplashAfterPaint();
    await vi.advanceTimersByTimeAsync(1);
    expect(document.getElementById("boot-splash")).toBeNull();
  });

  it("index.html's fade matches FADE_MS and turns off for reduced motion", () => {
    const m = /#boot-splash \{[^}]*transition: opacity ([\d.]+)s/.exec(indexHtml);
    expect(m && Math.round(Number(m[1]) * 1000)).toBe(FADE_MS);
    expect(indexHtml).toMatch(/prefers-reduced-motion: reduce\)\s*\{\s*#boot-splash \{ transition: none; \}/);
  });
});
