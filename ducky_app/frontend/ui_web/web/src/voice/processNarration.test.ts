import { describe, expect, it } from "vitest";

import {
  clampProcessTalk,
  shouldNarrateThinking,
  shouldNarrateThinkingDetail,
  shouldNarrateTool,
  shouldNarrateToolResult,
  speakableThinkingLine,
  speakableToolLine,
  thinkingTopic,
} from "./processNarration";

describe("processNarration", () => {
  it("clamps processTalk 0–1", () => {
    expect(clampProcessTalk(-1)).toBe(0);
    expect(clampProcessTalk(2)).toBe(1);
    expect(clampProcessTalk(0.5)).toBe(0.5);
  });

  it("gates tools vs thinking by level", () => {
    expect(shouldNarrateTool(0)).toBe(false);
    expect(shouldNarrateTool(0.1)).toBe(true);
    expect(shouldNarrateThinking(0.3)).toBe(false);
    expect(shouldNarrateThinking(0.4)).toBe(true);
    expect(shouldNarrateThinkingDetail(0.6)).toBe(false);
    expect(shouldNarrateThinkingDetail(0.7)).toBe(true);
    expect(shouldNarrateToolResult(0.3)).toBe(false);
    expect(shouldNarrateToolResult(0.4)).toBe(true);
  });

  it("builds short tool lines", () => {
    expect(speakableToolLine("workspace_read_file")).toBe("Running tool to read file.");
    expect(speakableToolLine("ping")).toBe("Running tool to checking uefn listener.");
  });

  it("builds thinking lines without dumping everything", () => {
    expect(speakableThinkingLine("hello world", 0.5)).toBe("Thinking.");
    expect(speakableThinkingLine("I should inspect the spawn pads next", 1)).toBe(
      "Thinking about I should inspect the spawn pads next.",
    );
    expect(thinkingTopic("a b c d e f g h i j k")).toBe("a b c d e f g h");
  });
});
