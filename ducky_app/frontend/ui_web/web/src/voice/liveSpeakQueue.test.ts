import { beforeEach, describe, expect, it, vi } from "vitest";

const listeners = new Set<(s: "idle" | "speaking" | "paused") => void>();
let speaking = false;

const liveChatMock = vi.hoisted(() => ({ ids: new Set<string>(["c1"]) }));

vi.mock("./liveChats", () => ({
  getLiveVoiceChatIds: () => liveChatMock.ids,
}));

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

const opts = (chatId = "c1", speaker = "") => ({ chatId, speaker, voiceId: "v", rate: 1 });

function autoIdleSpeak() {
  speaking = true;
  for (const fn of listeners) fn("speaking");
  queueMicrotask(() => {
    speaking = false;
    for (const fn of listeners) fn("idle");
  });
}

describe("liveSpeakQueue", () => {
  beforeEach(async () => {
    speaking = false;
    listeners.clear();
    liveChatMock.ids = new Set(["c1"]);
    vi.resetModules();
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(autoIdleSpeak);
    (ttsEngine.cancel as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = false;
      for (const fn of listeners) fn("idle");
    });
    const { clearLiveSpeakQueue } = await import("./liveSpeakQueue");
    clearLiveSpeakQueue();
  });

  it("plays process lines then final in order and never drops", async () => {
    const { enqueueProcessSpeak, enqueueFinalSpeak, liveSpeakQueueLength } = await import("./liveSpeakQueue");
    const { ttsEngine } = await import("./ttsEngine");

    enqueueProcessSpeak("Running tool to ping.", opts());
    enqueueProcessSpeak("Thinking.", opts());
    enqueueFinalSpeak("All done.", opts());

    await vi.waitFor(() => {
      expect(liveSpeakQueueLength()).toBe(0);
      expect(speaking).toBe(false);
    });

    expect(ttsEngine.speak).toHaveBeenCalledTimes(3);
    expect(ttsEngine.speak).toHaveBeenNthCalledWith(1, "Running tool to ping.", "v", 1);
    expect(ttsEngine.speak).toHaveBeenNthCalledWith(2, "Thinking.", "v", 1);
    expect(ttsEngine.speak).toHaveBeenNthCalledWith(3, "All done.", "v", 1);
  });

  it("keeps all waiting lines (no trim)", async () => {
    const speakFns = listeners;
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = true;
      for (const fn of speakFns) fn("speaking");
    });

    const mod = await import("./liveSpeakQueue");
    for (const word of ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]) {
      mod.enqueueProcessSpeak(word, opts());
    }

    expect(mod._liveSpeakQueueSnapshot().kinds).toHaveLength(10);
    expect(mod._liveSpeakQueueSnapshot().kinds.every((k) => k === "process")).toBe(true);
    expect(mod.getLiveSpeakTransport().behind).toBe(9);

    mod.clearLiveSpeakQueue();
  });

  it("resolves lazy text only when the line is played", async () => {
    const speakFns = listeners;
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = true;
      for (const fn of speakFns) fn("speaking");
    });

    const thunk = vi.fn(async () => "from thunk");
    const mod = await import("./liveSpeakQueue");
    mod.enqueueProcessSpeak("one", opts());
    mod.enqueueProcessSpeak(thunk, opts(), "fallback");
    mod.enqueueProcessSpeak("three", opts());

    expect(thunk).not.toHaveBeenCalled();
    mod.speakNewest();
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenLastCalledWith("three", "v", 1);
    });
    expect(thunk).not.toHaveBeenCalled();
    expect(mod._liveSpeakQueueSnapshot().resolved[1]).toBeNull();

    mod.clearLiveSpeakQueue();
  });

  it("speakNext advances exactly one line", async () => {
    const speakFns = listeners;
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = true;
      for (const fn of speakFns) fn("speaking");
    });

    const mod = await import("./liveSpeakQueue");
    mod.enqueueProcessSpeak("one", opts());
    mod.enqueueProcessSpeak("two", opts());
    mod.enqueueProcessSpeak("three", opts());
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("one", "v", 1);
    });

    mod.speakNext();
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenLastCalledWith("two", "v", 1);
    });
    expect(mod._liveSpeakQueueSnapshot().cursor).toBe(1);

    mod.clearLiveSpeakQueue();
  });

  it("speakPrev replays the previous line then continues forward", async () => {
    const speakFns = listeners;
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = true;
      for (const fn of speakFns) fn("speaking");
    });

    const mod = await import("./liveSpeakQueue");
    mod.enqueueProcessSpeak("one", opts());
    mod.enqueueProcessSpeak("two", opts());
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("one", "v", 1);
    });

    speaking = false;
    for (const fn of speakFns) fn("idle");
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenLastCalledWith("two", "v", 1);
    });

    mod.speakPrev();
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("one", "v", 1);
    });
    expect(mod.hasPrev()).toBe(true);

    mod.clearLiveSpeakQueue();
  });

  it("speakNewest lands on the last line and keeps history", async () => {
    const speakFns = listeners;
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = true;
      for (const fn of speakFns) fn("speaking");
    });

    const mod = await import("./liveSpeakQueue");
    mod.enqueueProcessSpeak("one", opts());
    mod.enqueueProcessSpeak("two", opts());
    mod.enqueueProcessSpeak("three", opts());
    mod.speakNewest();
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenLastCalledWith("three", "v", 1);
    });
    expect(mod.getLiveSpeakTransport().hasPrev).toBe(true);
    expect(mod.getLiveSpeakTransport().hasNewer).toBe(false);
    expect(mod._liveSpeakQueueSnapshot().kinds).toHaveLength(3);

    mod.clearLiveSpeakQueue();
  });

  it("clearLiveSpeakQueueForChat leaves other chats intact", async () => {
    const speakFns = listeners;
    const { ttsEngine } = await import("./ttsEngine");
    (ttsEngine.speak as ReturnType<typeof vi.fn>).mockImplementation(() => {
      speaking = true;
      for (const fn of speakFns) fn("speaking");
    });

    const mod = await import("./liveSpeakQueue");
    liveChatMock.ids = new Set(["a", "b"]);
    mod.enqueueProcessSpeak("a1", opts("a", "Ada"));
    mod.enqueueProcessSpeak("b1", opts("b", "Bea"));
    mod.enqueueProcessSpeak("a2", opts("a", "Ada"));
    mod.clearLiveSpeakQueueForChat("a");

    speaking = false;
    for (const fn of speakFns) fn("idle");
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("Bea: b1", "v", 1);
    });
    const snap = mod._liveSpeakQueueSnapshot();
    expect(snap.skipped[2]).toBe(true);

    mod.clearLiveSpeakQueue();
  });

  it("plays a line appended after the pump went idle", async () => {
    const { enqueueProcessSpeak } = await import("./liveSpeakQueue");
    const { ttsEngine } = await import("./ttsEngine");

    enqueueProcessSpeak("one", opts());
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("one", "v", 1);
      expect(speaking).toBe(false);
    });
    enqueueProcessSpeak("two", opts());
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("two", "v", 1);
    });
  });

  it("prefixes the speaker only when more than one chat is live", async () => {
    const { enqueueProcessSpeak } = await import("./liveSpeakQueue");
    const { ttsEngine } = await import("./ttsEngine");

    enqueueProcessSpeak("hello", { ...opts(), speaker: "Wizard" });
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("hello", "v", 1);
    });
  });

  it("prefixes the speaker when two chats are live", async () => {
    liveChatMock.ids = new Set(["c1", "c2"]);
    const { enqueueProcessSpeak } = await import("./liveSpeakQueue");
    const { ttsEngine } = await import("./ttsEngine");

    enqueueProcessSpeak("hello", { ...opts(), speaker: "Wizard" });
    await vi.waitFor(() => {
      expect(ttsEngine.speak).toHaveBeenCalledWith("Wizard: hello", "v", 1);
    });
  });
});
