import { describe, expect, it, beforeEach } from "vitest";
import {
  chatTurnElapsedMs,
  formatElapsedMs,
  getChatTurnTimer,
  markChatTurnIdle,
  markChatTurnRunning,
  syncChatTurnTimersFromRunningSet,
} from "./chatTurnTimer";

describe("formatElapsedMs", () => {
  it("formats sub-second and multi-minute spans", () => {
    expect(formatElapsedMs(42)).toBe("42ms");
    expect(formatElapsedMs(1500)).toBe("1.5s");
    expect(formatElapsedMs(12_000)).toBe("12s");
    expect(formatElapsedMs(65_000)).toBe("1m 05s");
    expect(formatElapsedMs(3_661_000)).toBe("1h 1m");
  });
});

describe("chatTurnTimer store", () => {
  beforeEach(() => {
    // Reset by completing any leftover turns and starting fresh ids.
    markChatTurnIdle("a");
    markChatTurnIdle("b");
  });

  it("starts a clock and freezes it on idle", () => {
    markChatTurnRunning("a", 1_000);
    expect(getChatTurnTimer("a")).toEqual({ startedAt: 1_000, endedAt: null });
    expect(chatTurnElapsedMs(getChatTurnTimer("a"), 1_500)).toBe(500);

    markChatTurnIdle("a", 2_500);
    const done = getChatTurnTimer("a");
    expect(done).toEqual({ startedAt: 1_000, endedAt: 2_500 });
    expect(chatTurnElapsedMs(done, 9_999)).toBe(1_500);
  });

  it("does not restart while already running", () => {
    markChatTurnRunning("a", 1_000);
    markChatTurnRunning("a", 9_999);
    expect(getChatTurnTimer("a")?.startedAt).toBe(1_000);
  });

  it("starts a new turn after a finished one", () => {
    markChatTurnRunning("a", 1_000);
    markChatTurnIdle("a", 2_000);
    markChatTurnRunning("a", 5_000);
    expect(getChatTurnTimer("a")).toEqual({ startedAt: 5_000, endedAt: null });
  });

  it("syncs from running-set diffs", () => {
    syncChatTurnTimersFromRunningSet(new Set(), new Set(["b"]), 100);
    expect(getChatTurnTimer("b")?.endedAt).toBeNull();
    syncChatTurnTimersFromRunningSet(new Set(["b"]), new Set(), 400);
    expect(getChatTurnTimer("b")).toEqual({ startedAt: 100, endedAt: 400 });
  });
});
