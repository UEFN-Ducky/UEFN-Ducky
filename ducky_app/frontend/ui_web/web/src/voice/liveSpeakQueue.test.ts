import { beforeEach, describe, expect, it, vi } from "vitest";

const listeners = new Set<(s: "idle" | "speaking" | "paused") => void>();
let speaking = false;

vi.mock("./ttsEngine", () => ({
  ttsEngine: {
    isSpeaking: () => speaking,
    speak: vi.fn((..._args: unknown[]) => {
      speaking = true;
      for (const fn of listeners) fn("speaking");
      queueMicrotask(() => {
        speaking = false;
        for (const fn of listeners) fn("idle");
      });
    }),
    cancel: vi.fn(() => {
      speaking = false;
      for (const fn of listeners) fn("idle");
    }),
    onStateChange: (fn: (s: "idle" | "speaking" | "paused") => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  },
}));

describe("liveSpeakQueue", () => {
  beforeEach(async () => {
    speaking = false;
    listeners.clear();
    vi.clearAllMocks();
    vi.resetModules();
    const { clearLiveSpeakQueue } = await import("./liveSpeakQueue");
    clearLiveSpeakQueue();
  });

  it("plays process lines then final in order", async () => {
    const { enqueueProcessSpeak, enqueueFinalSpeak, liveSpeakQueueLength } = await import("./liveSpeakQueue");
    const { ttsEngine } = await import("./ttsEngine");

    enqueueProcessSpeak("Running tool to ping.", "v", 1);
    enqueueProcessSpeak("Thinking.", "v", 1);
    enqueueFinalSpeak("All done.", "v", 1);

    await vi.waitFor(() => {
      expect(liveSpeakQueueLength()).toBe(0);
      expect(speaking).toBe(false);
    });

    expect(ttsEngine.speak).toHaveBeenCalledTimes(3);
    expect(ttsEngine.speak).toHaveBeenNthCalledWith(1, "Running tool to ping.", "v", 1);
    expect(ttsEngine.speak).toHaveBeenNthCalledWith(2, "Thinking.", "v", 1);
    expect(ttsEngine.speak).toHaveBeenNthCalledWith(3, "All done.", "v", 1);
  });

  it("keeps at most 2 waiting process lines", async () => {
    // Block idle so the pump holds on the first line.
    const speakFns = listeners;
    speaking = false;
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = true;
      for (const fn of speakFns) fn("speaking");
      // no auto-idle
    });

    const mod = await import("./liveSpeakQueue");
    mod.enqueueProcessSpeak("one", "v", 1);
    mod.enqueueProcessSpeak("two", "v", 1);
    mod.enqueueProcessSpeak("three", "v", 1);
    mod.enqueueProcessSpeak("four", "v", 1);

    // Current line playing + ≤2 waiting process.
    expect(mod._liveSpeakQueueSnapshot().kinds.length).toBeLessThanOrEqual(2);
    expect(mod._liveSpeakQueueSnapshot().kinds.every((k) => k === "process")).toBe(true);

    mod.clearLiveSpeakQueue();
  });
});
