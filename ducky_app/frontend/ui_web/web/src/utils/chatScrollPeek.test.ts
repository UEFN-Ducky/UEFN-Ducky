import { describe, expect, it } from "vitest";

import type { ChatTurn } from "./chatMessageGroups";
import {
  flattenPeekText,
  indexAtFraction,
  indexAtOffset,
  peekLinesForTurn,
  turnOffsetsFromChunkHeights,
  peekTickLayout,
} from "./chatScrollPeek";

function turn(query: string, reply: string, more = ""): ChatTurn {
  return {
    id: `turn-${query}`,
    query: { kind: "bubble", id: query, role: "user", text: query },
    responses: [
      { kind: "bubble", id: `${query}-a`, role: "assistant", text: reply },
      ...(more ? [{ kind: "bubble" as const, id: `${query}-b`, role: "assistant" as const, text: more }] : []),
    ],
  };
}

describe("chatScrollPeek", () => {
  it("flattens markdown and truncates", () => {
    expect(flattenPeekText("**hello**   world", 20)).toBe("hello world");
    expect(flattenPeekText("a".repeat(80), 10)).toBe(`${"a".repeat(9)}…`);
  });

  it("pulls query + first two reply lines off a turn", () => {
    expect(peekLinesForTurn(turn("why css", "it leaked", "I traced paint"))).toEqual({
      query: "why css",
      reply: "it leaked",
      more: "I traced paint",
    });
  });

  it("splits chunk heights across the turns in that chunk", () => {
    expect(turnOffsetsFromChunkHeights([800, 400], 2, 3)).toEqual([0, 400, 800]);
  });

  it("maps a Y and a fraction onto a turn index", () => {
    expect(indexAtOffset([0, 400, 800], 400)).toBe(1);
    expect(indexAtOffset([0, 400, 800], 799)).toBe(1);
    expect(indexAtFraction(0, 10)).toBe(0);
    expect(indexAtFraction(0.99, 10)).toBe(9);
    expect(indexAtFraction(1, 10)).toBe(9);
  });

  it("packs ticks at a tight gap instead of stretching the stack", () => {
    const layout = peekTickLayout(8, 800, 3);
    expect(layout.indexes).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(layout.stackH).toBe(21);
    expect(layout.gap).toBe(3);
    expect(layout.start).toBe((800 - 21) / 2);
  });

  it("thins ticks that would stack closer than the gap", () => {
    const layout = peekTickLayout(100, 40, 5);
    expect(layout.indexes[0]).toBe(0);
    expect(layout.indexes[layout.indexes.length - 1]).toBe(99);
    expect(layout.indexes.length).toBeLessThan(100);
    expect(layout.stackH).toBeLessThanOrEqual(40);
  });
});
