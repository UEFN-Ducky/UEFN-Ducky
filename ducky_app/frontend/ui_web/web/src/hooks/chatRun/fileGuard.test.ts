import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../../types/panel";
import { chatRunReducer, fileGuardRowText, initialRunState, type RunAction } from "./chatRunReducer";

function ev(event: AgentEvent): RunAction {
  return { type: "agentEvent", event };
}

describe("file_guard events", () => {
  it("formats the row text per kind", () => {
    expect(fileGuardRowText({ type: "file_guard", kind: "lane_denied", text: "Out of lane: x" })).toBe(
      "⛔ Out of lane: Out of lane: x",
    );
    expect(fileGuardRowText({ type: "file_guard", kind: "conflict", text: "a.verse was changed by Solo" })).toBe(
      "⚠ Conflict: a.verse was changed by Solo",
    );
    expect(fileGuardRowText({ type: "file_guard", kind: "shadow_violation", path: "Content/Verse/Hub/h.verse" })).toBe(
      "⚠ Out of lane (allowed in shadow mode): Content/Verse/Hub/h.verse",
    );
    expect(fileGuardRowText({ type: "file_guard", kind: "policy_denied", text: "nope" })).toBe("⛔ Write refused: nope");
  });

  it("appends an error row that keeps the group author", () => {
    const s = chatRunReducer(
      initialRunState,
      ev({ type: "file_guard", kind: "lane_denied", text: "Out of lane", author: { name: "Hacker" } }),
    );
    const last = s.messages.at(-1);
    expect(last?.role).toBe("error");
    expect(last?.text).toBe("⛔ Out of lane: Out of lane");
    expect(last?.author?.name).toBe("Hacker");
    expect(s.hasNewBelow).toBe(false);
  });
});
