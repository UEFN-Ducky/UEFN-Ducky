import { describe, expect, it } from "vitest";
import { swipeShouldOpen, swipeSideFromPoint, swipeTranslateX, RAIL_EDGE_PX } from "./railEdgeSwipe";

describe("railEdgeSwipe", () => {
  it("slides a closed left rail from -width toward 0", () => {
    expect(swipeTranslateX("left", false, 0, 200)).toBe(-200);
    expect(swipeTranslateX("left", false, 80, 200)).toBe(-120);
    expect(swipeTranslateX("left", false, 200, 200)).toBe(0);
    expect(swipeTranslateX("left", false, 400, 200)).toBe(0);
  });

  it("slides an open left rail from 0 toward -width", () => {
    expect(swipeTranslateX("left", true, 0, 200)).toBe(0);
    expect(swipeTranslateX("left", true, -80, 200)).toBe(-80);
    expect(swipeTranslateX("left", true, -200, 200)).toBe(-200);
  });

  it("slides a closed right rail from +width toward 0", () => {
    expect(swipeTranslateX("right", false, 0, 200)).toBe(200);
    expect(swipeTranslateX("right", false, -80, 200)).toBe(120);
    expect(swipeTranslateX("right", false, -200, 200)).toBe(0);
  });

  it("snaps open past halfway or with a fling", () => {
    expect(swipeShouldOpen("left", -80, 200, 0)).toBe(true);
    expect(swipeShouldOpen("left", -140, 200, 0)).toBe(false);
    expect(swipeShouldOpen("left", -140, 200, 0.5)).toBe(true);
    expect(swipeShouldOpen("right", 80, 200, 0)).toBe(true);
    expect(swipeShouldOpen("right", 140, 200, 0)).toBe(false);
    expect(swipeShouldOpen("right", 140, 200, -0.5)).toBe(true);
  });

  it("picks an edge or an already-open rail", () => {
    expect(swipeSideFromPoint(10, 0, 400, false, false, true, true, 200, 200)).toBe("left");
    expect(swipeSideFromPoint(390, 0, 400, false, false, true, true, 200, 200)).toBe("right");
    expect(swipeSideFromPoint(200, 0, 400, false, false, true, true, 200, 200)).toBe(null);
    expect(swipeSideFromPoint(50, 0, 400, true, false, true, true, 200, 200)).toBe("left");
    expect(RAIL_EDGE_PX).toBe(24);
  });
});
