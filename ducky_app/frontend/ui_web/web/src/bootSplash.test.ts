// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dismissBootSplashAfterPaint } from "./bootSplash";

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
});
