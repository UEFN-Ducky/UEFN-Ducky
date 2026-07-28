import { describe, expect, it } from "vitest";
import { stackedPanelDropHint } from "./stackedPanelDropHint";

const stack = [
  { id: "content", top: 0, bottom: 400, collapsed: false },
  { id: "duckies", top: 400, bottom: 428, collapsed: true },
  { id: "outline", top: 428, bottom: 456, collapsed: true },
  { id: "history", top: 456, bottom: 484, collapsed: true },
] as const;

describe("stackedPanelDropHint", () => {
  it("shows a line on a collapsed neighbor without needing its midpoint", () => {
    // Drag duckies down; pointer just into outline's top (above midpoint).
    expect(stackedPanelDropHint([...stack], "duckies", 430)).toEqual({
      targetId: "outline",
      edge: "before",
    });
  });

  it("shows a line when dragging a collapsed panel up over another collapsed one", () => {
    expect(stackedPanelDropHint([...stack], "history", 440)).toEqual({
      targetId: "outline",
      edge: "after",
    });
  });

  it("still requires midpoint for expanded panels", () => {
    // Duckies sits below Content — only the upper half of Content accepts the drop.
    expect(stackedPanelDropHint([...stack], "duckies", 250)).toBeNull();
    expect(stackedPanelDropHint([...stack], "duckies", 100)).toEqual({
      targetId: "content",
      edge: "after",
    });
  });

  it("treats the gap between collapsed headers as a hit", () => {
    expect(stackedPanelDropHint([...stack], "duckies", 427)).toEqual({
      targetId: "outline",
      edge: "before",
    });
  });
});
