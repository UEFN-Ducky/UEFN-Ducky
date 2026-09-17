import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CachedChatMessagesState } from "./chatMessagesCache";

function snapshot(text = "message", running = false): CachedChatMessagesState {
  return {
    messages: [{ id: 1, role: "assistant", text }],
    streamBuffer: "", streamThinking: "", optimisticRunning: running,
    hasNewBelow: false, isAtBottom: true, activeRunId: running ? "run" : null, stoppedRun: false,
  };
}

describe("chat snapshot retention", () => {
  beforeEach(() => vi.resetModules());

  it("evicts by estimated bytes before reaching the conversation count limit", async () => {
    const cache = await import("./chatMessagesCache");
    const large = "x".repeat(4_500_000);
    cache.setCachedChatMessages("older", snapshot(large));
    cache.setCachedChatMessages("newer", snapshot(large));
    expect(cache.getCachedChatMessages("older")).toBeUndefined();
    expect(cache.getCachedChatMessages("newer")?.messages[0].text).toBe(large);
  });

  it("preserves unsaved live streams and releases an oversized snapshot after completion", async () => {
    const cache = await import("./chatMessagesCache");
    const active = snapshot("x".repeat(9_000_000), true);
    cache.setCachedChatMessages("live", active);
    cache.setCachedChatMessages("idle", snapshot());
    expect(cache.getCachedChatMessages("live")).toBe(active);
    cache.setCachedChatMessages("live", { ...active, optimisticRunning: false });
    expect(cache.getCachedChatMessages("live")).toBeUndefined();
    expect(cache.getCachedChatMessages("idle")).toBeDefined();
    // Eviction never mutates messages owned by the mounted conversation.
    expect(active.messages[0].text.length).toBe(9_000_000);
  });

  it("counts nested tool arguments and file diffs, not just visible text", async () => {
    const cache = await import("./chatMessagesCache");
    const state = snapshot();
    state.messages[0].tool = { name: "edit", arguments: { code: "x".repeat(9_000_000) } };
    cache.setCachedChatMessages("large-tool", state);
    expect(cache.getCachedChatMessages("large-tool")).toBeUndefined();
  });

  it("refreshes recency on reads and keeps the existing 24-chat cap", async () => {
    const cache = await import("./chatMessagesCache");
    for (let i = 0; i < 24; i++) cache.setCachedChatMessages(`${i}`, snapshot());
    cache.getCachedChatMessages("0");
    cache.setCachedChatMessages("24", snapshot());
    expect(cache.getCachedChatMessages("1")).toBeUndefined();
    expect(cache.getCachedChatMessages("0")).toBeDefined();
  });

  it("updates accounting on replacement and explicit deletion", async () => {
    const cache = await import("./chatMessagesCache");
    cache.setCachedChatMessages("a", snapshot("x".repeat(4_500_000)));
    cache.setCachedChatMessages("a", snapshot());
    cache.setCachedChatMessages("b", snapshot("x".repeat(4_500_000)));
    expect(cache.getCachedChatMessages("a")).toBeDefined();
    cache.clearCachedChatMessages("b");
    cache.setCachedChatMessages("c", snapshot("x".repeat(4_500_000)));
    expect(cache.getCachedChatMessages("a")).toBeDefined();
  });
});
