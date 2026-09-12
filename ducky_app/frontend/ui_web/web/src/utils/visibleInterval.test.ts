import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setVisibleInterval } from "./visibleInterval";

/** Minimal document stand-in: vitest runs this suite in node. */
function installDocument(hidden = false) {
  const listeners = new Set<() => void>();
  const doc = {
    hidden,
    addEventListener: (type: string, fn: () => void) => {
      if (type === "visibilitychange") listeners.add(fn);
    },
    removeEventListener: (type: string, fn: () => void) => {
      if (type === "visibilitychange") listeners.delete(fn);
    },
  };
  vi.stubGlobal("document", doc);
  vi.stubGlobal("window", { setInterval, clearInterval });
  return {
    doc,
    show() {
      doc.hidden = false;
      listeners.forEach((fn) => fn());
    },
    hide() {
      doc.hidden = true;
      listeners.forEach((fn) => fn());
    },
    listenerCount: () => listeners.size,
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("setVisibleInterval", () => {
  it("polls while the window is visible", () => {
    installDocument(false);
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 1000);
    vi.advanceTimersByTime(3000);
    expect(tick).toHaveBeenCalledTimes(3);
    stop();
  });

  it("stops polling while hidden and does not accumulate missed ticks", () => {
    const env = installDocument(false);
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 1000);
    vi.advanceTimersByTime(2000);
    expect(tick).toHaveBeenCalledTimes(2);

    env.hide();
    vi.advanceTimersByTime(60_000);
    expect(tick).toHaveBeenCalledTimes(2); // an hour hidden costs nothing
    stop();
  });

  it("refreshes once immediately when the window comes back", () => {
    const env = installDocument(false);
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 1000);
    env.hide();
    vi.advanceTimersByTime(10_000);
    expect(tick).toHaveBeenCalledTimes(0);

    env.show();
    expect(tick).toHaveBeenCalledTimes(1); // fresh on return, not one interval stale
    vi.advanceTimersByTime(1000);
    expect(tick).toHaveBeenCalledTimes(2);
    stop();
  });

  it("does not start while already hidden", () => {
    installDocument(true);
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 500);
    vi.advanceTimersByTime(5000);
    expect(tick).not.toHaveBeenCalled();
    stop();
  });

  it("cancelling removes the timer and the visibility listener", () => {
    const env = installDocument(false);
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 1000);
    expect(env.listenerCount()).toBe(1);
    stop();
    vi.advanceTimersByTime(5000);
    expect(tick).not.toHaveBeenCalled();
    expect(env.listenerCount()).toBe(0);
    // A cancelled poll must stay dead even if visibility changes later.
    env.hide();
    env.show();
    vi.advanceTimersByTime(5000);
    expect(tick).not.toHaveBeenCalled();
  });
});
