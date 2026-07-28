import { describe, expect, it } from "vitest";

import {
  isPlanPaused,
  isPlanStarted,
  isPlanStructureLocked,
  isNodeDone,
  nodeKind,
} from "./planLock";

describe("isPlanStructureLocked", () => {
  it("locks finished plans", () => {
    expect(isPlanStructureLocked({ status: "finished", todos: [] })).toBe(true);
  });

  it("locks when a node is in progress (playing)", () => {
    expect(
      isPlanStructureLocked({
        status: "open",
        nodes: [{ id: "a", content: "x", status: "in_progress", kind: "step" }],
        todos: [],
      }),
    ).toBe(true);
  });

  it("unlocks unfinished edits when paused", () => {
    expect(
      isPlanStructureLocked({
        status: "paused",
        nodes: [{ id: "a", content: "x", status: "completed", kind: "step" }],
        todos: [],
      }),
    ).toBe(false);
  });

  it("allows untouched open plans", () => {
    expect(
      isPlanStructureLocked({
        status: "open",
        nodes: [{ id: "a", content: "x", status: "pending", kind: "step" }],
        todos: [],
      }),
    ).toBe(false);
  });
});

describe("isPlanPaused / isPlanStarted / isNodeDone", () => {
  it("detects paused status", () => {
    expect(isPlanPaused({ status: "paused" })).toBe(true);
    expect(isPlanPaused({ status: "open" })).toBe(false);
  });

  it("detects started from completed nodes", () => {
    expect(
      isPlanStarted({
        status: "open",
        nodes: [{ id: "a", content: "x", status: "completed", kind: "step" }],
        todos: [],
      }),
    ).toBe(true);
  });

  it("detects done nodes", () => {
    expect(isNodeDone({ status: "completed" })).toBe(true);
    expect(isNodeDone({ status: "pending" })).toBe(false);
  });
});

describe("nodeKind", () => {
  it("infers subplan from children", () => {
    expect(
      nodeKind({
        id: "1",
        content: "A",
        status: "pending",
        children: [{ id: "2", content: "B", status: "pending" }],
      }),
    ).toBe("subplan");
  });
});
