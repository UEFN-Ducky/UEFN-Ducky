import { describe, expect, it } from "vitest";
import { railResizeEnd, railResizeFrame } from "./dockRailResize";

describe("railResizeFrame", () => {
  it("lets the left rail shrink to 0 instead of stopping at min", () => {
    expect(railResizeFrame("left", 180, -180, 560)).toBe(0);
    expect(railResizeFrame("left", 240, 400, 560)).toBe(560);
  });

  it("lets the right rail shrink to 0 (drag handle right)", () => {
    expect(railResizeFrame("right", 180, 180, 480)).toBe(0);
    expect(railResizeFrame("right", 260, -400, 480)).toBe(480);
  });
});

describe("railResizeEnd", () => {
  it("closes at 0 and restores the width from before the drag", () => {
    expect(railResizeEnd(0, 180, 560, 240)).toEqual({ close: true, width: 240 });
  });

  it("closes when the handle is dragged past the collapse width", () => {
    expect(railResizeEnd(40, 180, 560, 320)).toEqual({ close: true, width: 320 });
  });

  it("never persists 0 as the restored width", () => {
    expect(railResizeEnd(0, 180, 560, 0)).toEqual({ close: true, width: 180 });
  });

  it("snaps back to min when released between collapse and min", () => {
    expect(railResizeEnd(120, 180, 560, 240)).toEqual({ close: false, width: 180 });
  });

  it("keeps an in-range width", () => {
    expect(railResizeEnd(240, 180, 560, 240)).toEqual({ close: false, width: 240 });
  });
});
