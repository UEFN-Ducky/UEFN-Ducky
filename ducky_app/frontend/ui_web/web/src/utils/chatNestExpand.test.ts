import { describe, expect, it } from "vitest";

import {
  chatNestDefaultExpanded,
  isSubagentComposerLocked,
} from "./chatNestExpand";

describe("chatNestDefaultExpanded", () => {
  it("always expands nested stacks (subagent collapse retired)", () => {
    expect(chatNestDefaultExpanded(true)).toBe(true);
    expect(chatNestDefaultExpanded(false)).toBe(true);
  });
});

describe("isSubagentComposerLocked", () => {
  it("never locks (subagents retired)", () => {
    expect(isSubagentComposerLocked({ isSubagent: true })).toBe(false);
    expect(isSubagentComposerLocked({})).toBe(false);
    expect(isSubagentComposerLocked({ isSubagent: false })).toBe(false);
  });
});
