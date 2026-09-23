import { describe, expect, it } from "vitest";
import {
  flexGrowForStackedPanel,
  resizeStackedPanelSplit,
  resolveStackedPanelFlex,
} from "./stackedPanelFlex";

const base = {
  order: ["a", "b", "c"] as const,
  collapsed: { a: false, b: false, c: false },
  splitRatio: 0.5,
  minPanelHeight: 80,
};

describe("stackedPanelFlex", () => {
  it("splits three open panels evenly when no custom flex is saved", () => {
    const flex = resolveStackedPanelFlex(base);
    expect(flex.get("a")).toBeCloseTo(1 / 3);
    expect(flex.get("b")).toBeCloseTo(1 / 3);
    expect(flex.get("c")).toBeCloseTo(1 / 3);
  });

  it("resizes only the pair above and below the dragged split", () => {
    const first = resizeStackedPanelSplit(base, 0, 100, 1000);
    expect(first.panelFlex.a).toBeGreaterThan(1 / 3);
    expect(first.panelFlex.b).toBeLessThan(1 / 3);
    expect(first.panelFlex.c).toBeCloseTo(1 / 3, 5);

    const second = resizeStackedPanelSplit(
      { ...base, panelFlex: first.panelFlex, splitRatio: first.splitRatio },
      1,
      -50,
      1000,
    );

    expect(second.panelFlex.a).toBeCloseTo(first.panelFlex.a!, 5);
    expect(second.panelFlex.b).toBeLessThan(first.panelFlex.b!);
    expect(second.panelFlex.c).toBeGreaterThan(first.panelFlex.c!);
  });

  it("keeps two-panel splitRatio in sync", () => {
    const twoPanel = {
      ...base,
      order: ["a", "b"] as const,
      collapsed: { a: false, b: false, c: true },
    };
    const next = resizeStackedPanelSplit(twoPanel, 0, 120, 1000);
    expect(next.splitRatio).toBeCloseTo(next.panelFlex.a ?? 0, 5);
    expect(flexGrowForStackedPanel({ ...twoPanel, ...next }, "b")).toBeCloseTo(
      1 - (next.panelFlex.a ?? 0),
      5,
    );
  });

  it("tracks pixels using the rendered open heights, excluding collapsed headers and sashes", () => {
    const input = { ...base, collapsed: { a: false, b: true, c: false } };
    const snapshot = { order: ["a", "b", "c"], panelHeights: { a: 300, c: 200 } };
    const next = resizeStackedPanelSplit(input, 0, 60, 500, snapshot);
    const weights = resolveStackedPanelFlex({ ...input, ...next });
    expect(weights.get("a")! * 500).toBeCloseTo(360);
    expect(weights.get("c")! * 500).toBeCloseTo(140);
    expect(resizeStackedPanelSplit(input, 1, 60, 500, snapshot)).toEqual(next);
  });

  it("pushes farther panels after the nearest panel reaches its minimum", () => {
    const snapshot = { order: ["a", "b", "c"], panelHeights: { a: 200, b: 100, c: 300 } };
    const next = resizeStackedPanelSplit(base, 0, 120, 600, snapshot);
    const weights = resolveStackedPanelFlex({ ...base, ...next });
    expect(weights.get("a")! * 600).toBeCloseTo(320);
    expect(weights.get("b")! * 600).toBeCloseTo(80);
    expect(weights.get("c")! * 600).toBeCloseTo(200);
  });

  it("clamps the transfer without changing the total or unrelated panel sizes", () => {
    const input = { ...base, panelFlex: { a: 2, b: 1, c: 3 } };
    const next = resizeStackedPanelSplit(input, 0, 10000, 600);
    expect(next.panelFlex.a).toBeCloseTo(4.4);
    expect(next.panelFlex.b).toBeCloseTo(0.8);
    expect(next.panelFlex.c).toBeCloseTo(0.8);
    expect(Object.values(next.panelFlex).reduce((a, b) => a + b, 0)).toBeCloseTo(6);
    const up = resizeStackedPanelSplit(input, 1, -10000, 600);
    expect(up.panelFlex.a).toBeCloseTo(0.8);
    expect(up.panelFlex.b).toBeCloseTo(0.8);
    expect(up.panelFlex.c).toBeCloseTo(4.4);
  });

  it("uses total movement from the grab point, including reversal and return to origin", () => {
    const snapshot = { order: ["a", "b", "c"], panelHeights: { a: 200, b: 100, c: 300 } };
    const first = resizeStackedPanelSplit(base, 0, 1000, 600, snapshot);
    const reversed = resizeStackedPanelSplit({ ...base, ...first }, 0, 10, 600, snapshot);
    expect(resolveStackedPanelFlex({ ...base, ...reversed }).get("a")! * 600).toBeCloseTo(210);
    const origin = resizeStackedPanelSplit({ ...base, ...reversed }, 0, 0, 600, snapshot);
    expect(resolveStackedPanelFlex({ ...base, ...origin }).get("a")! * 600).toBeCloseTo(200);
  });

  it("uses the visible family order and preserves panels outside that stack", () => {
    const input = { ...base, panelFlex: { a: 2, b: 5, c: 3 } };
    const snapshot = { order: ["c", "a"], panelHeights: { c: 300, a: 200 } };
    const next = resizeStackedPanelSplit(input, 0, 50, 500, snapshot);
    expect(next.panelFlex.c).toBeCloseTo(3.5);
    expect(next.panelFlex.a).toBeCloseTo(1.5);
    expect(next.panelFlex.b).toBe(5);
  });

  it("does not jump when a crowded rail starts below the preferred minimum", () => {
    const snapshot = { order: ["a", "b", "c"], panelHeights: { a: 40, b: 50, c: 60 } };
    const next = resizeStackedPanelSplit(base, 0, 20, 150, snapshot);
    const weights = resolveStackedPanelFlex({ ...base, ...next });
    expect(weights.get("a")! * 150).toBeCloseTo(40);
    expect(weights.get("b")! * 150).toBeCloseTo(50);
    expect(weights.get("c")! * 150).toBeCloseTo(60);
  });
});
