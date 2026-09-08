import { describe, expect, it } from "vitest";

import { prefixOffsets, visibleWindow } from "./virtualWindow";

const heights = Array.from({ length: 100 }, (_, i) => (i % 10 === 0 ? 46 : 30));
const offsets = prefixOffsets(heights);

describe("visibleWindow", () => {
  it("renders only what is on screen, plus the overscan", () => {
    const win = visibleWindow(offsets, heights.length, 0, 300, 0);
    expect(win.start).toBe(0);
    // 46 + 9*30 = 316, so ten rows cover a 300px viewport.
    expect(win.end).toBe(10);
    expect(win.padTop).toBe(0);
    expect(win.padBottom).toBe(offsets[100] - offsets[10]);
  });

  it("the spacers always add up to the full list height", () => {
    const total = offsets[heights.length];
    for (const top of [0, 200, 1000, total]) {
      const win = visibleWindow(offsets, heights.length, top, 400, 120);
      const rendered = offsets[win.end] - offsets[win.start];
      expect(win.padTop + rendered + win.padBottom).toBe(total);
    }
  });

  it("scrolling down moves the window without changing the total", () => {
    const first = visibleWindow(offsets, heights.length, 0, 400, 0);
    const later = visibleWindow(offsets, heights.length, 1200, 400, 0);
    expect(later.start).toBeGreaterThan(first.end - 1);
    expect(later.padTop).toBe(offsets[later.start]);
  });

  it("overscan reaches back above the viewport", () => {
    const tight = visibleWindow(offsets, heights.length, 1200, 400, 0);
    const loose = visibleWindow(offsets, heights.length, 1200, 400, 300);
    expect(loose.start).toBeLessThan(tight.start);
    expect(loose.end).toBeGreaterThanOrEqual(tight.end);
  });

  it("an empty list renders nothing and needs no spacers", () => {
    expect(visibleWindow(prefixOffsets([]), 0, 0, 400, 100)).toEqual({
      start: 0,
      end: 0,
      padTop: 0,
      padBottom: 0,
    });
  });
});
