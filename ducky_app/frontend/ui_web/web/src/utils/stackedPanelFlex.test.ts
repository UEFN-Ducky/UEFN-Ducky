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
});
