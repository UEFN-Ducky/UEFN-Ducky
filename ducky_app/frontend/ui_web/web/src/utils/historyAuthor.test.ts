import { describe, expect, it } from "vitest";

import { historyAuthor } from "./historyAuthor";

describe("historyAuthor", () => {
  it("legacy and user versions are 'You'", () => {
    expect(historyAuthor({}).kind).toBe("you");
    expect(historyAuthor({ source: "" }).label).toBe("You");
    expect(historyAuthor({ source: "user" }).kind).toBe("you");
  });

  it("reverts are labelled", () => {
    expect(historyAuthor({ source: "revert" })).toEqual({
      kind: "revert",
      label: "Reverted",
      detail: "Restored by a changeset revert",
    });
  });

  it("agent versions carry the ducky name and model/tool detail", () => {
    const a = historyAuthor({
      source: "agent",
      ducky_name: "Hacker",
      model: "anthropic:claude-sonnet-5",
      tool: "workspace_write_file",
      coding_agent: "ducky",
    });
    expect(a.kind).toBe("ducky");
    expect(a.label).toBe("Hacker");
    expect(a.detail).toBe("Hacker · claude-sonnet-5 · workspace_write_file");
  });

  it("external coding agents are named", () => {
    const a = historyAuthor({ source: "agent", ducky_name: "Hacker", coding_agent: "claude_code", tool: "Write" });
    expect(a.detail).toBe("Hacker · Claude Code · Write");
    expect(historyAuthor({ source: "agent" }).label).toBe("AI");
  });
});
