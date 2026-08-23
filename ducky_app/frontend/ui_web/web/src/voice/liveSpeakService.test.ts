import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent } from "../types/panel";

const enqueueProcessSpeak = vi.fn();
const enqueueAnswerSpeak = vi.fn();
const enqueueFinalSpeak = vi.fn();
let eventHandler: ((event: AgentEvent) => void) | null = null;

vi.mock("./liveSpeakQueue", () => ({
  enqueueProcessSpeak: (...args: unknown[]) => enqueueProcessSpeak(...args),
  enqueueAnswerSpeak: (...args: unknown[]) => enqueueAnswerSpeak(...args),
  enqueueFinalSpeak: (...args: unknown[]) => enqueueFinalSpeak(...args),
  clearLiveSpeakQueueForChat: vi.fn(),
  getCurrentSpokenLine: () => null,
  getLiveSpeakTransport: () => ({
    hasPrev: false,
    hasNext: false,
    hasNewer: false,
    behind: 0,
    cursor: -1,
    length: 0,
  }),
  liveSpeakQueueLength: () => 0,
  parkLiveSpeakQueue: vi.fn(),
  subscribeLiveSpeakTransport: (fn: () => void) => {
    fn();
    return () => {};
  },
}));

vi.mock("../hooks/useAgentEventBus", () => ({
  installAgentEventBus: () => {},
  subscribeAgentEvents: (fn: (event: AgentEvent) => void) => {
    eventHandler = fn;
    return () => {
      eventHandler = null;
    };
  },
}));

vi.mock("./voiceSettings", () => ({
  getVoiceSettings: () => ({
    enabled: true,
    spokenStyle: "summary",
    summaryModel: "",
    defaultVoice: "v",
    defaultSpeed: 1,
    liveManualSend: false,
    processTalk: 0.7,
  }),
}));

vi.mock("./ttsEngine", () => ({
  ttsEngine: {
    onStateChange: () => () => {},
    isSpeaking: () => false,
  },
}));

vi.mock("./speakReply", () => ({
  loadLastAssistantText: async () => "",
  prepareSpokenText: async (t: string) => t,
  summarizeForSpeech: async (t: string) => t,
}));

describe("liveSpeakService", () => {
  beforeEach(async () => {
    enqueueProcessSpeak.mockClear();
    enqueueAnswerSpeak.mockClear();
    enqueueFinalSpeak.mockClear();
    eventHandler = null;
    vi.resetModules();
    const { _resetLiveSpeakService } = await import("./liveSpeakService");
    _resetLiveSpeakService();
  });

  it("enqueues a tool line with no mounted component", async () => {
    const { startLiveChat } = await import("./liveSpeakService");
    startLiveChat("chat-a", { voiceId: "v", speed: 1 });
    expect(eventHandler).toBeTruthy();
    eventHandler?.({
      type: "tool",
      conv_id: "chat-a",
      tool: { name: "workspace_write_file", arguments: { path: "Verse/prey.verse" } },
    });
    expect(enqueueProcessSpeak).toHaveBeenCalled();
    const line = enqueueProcessSpeak.mock.calls[0][0];
    expect(String(line)).toMatch(/writing the file prey dot verse/i);
  });

  it("keeps isLiveChat true after a simulated unmount (mic release)", async () => {
    const { startLiveChat, isLiveChat, releaseMic, claimMic } = await import("./liveSpeakService");
    startLiveChat("chat-a", { voiceId: "v", speed: 1 });
    expect(claimMic("chat-a")).toBe(true);
    releaseMic("chat-a");
    expect(isLiveChat("chat-a")).toBe(true);
  });

  it("keeps the live session when the mic is muted", async () => {
    const { startLiveChat, isLiveChat, releaseMic } = await import("./liveSpeakService");
    const { patchLiveVoiceState, getLiveVoiceState } = await import("./liveChats");
    startLiveChat("chat-a", { voiceId: "v", speed: 1 });
    patchLiveVoiceState("chat-a", { muted: true, status: "muted" });
    releaseMic("chat-a");
    expect(isLiveChat("chat-a")).toBe(true);
    expect(getLiveVoiceState("chat-a").muted).toBe(true);
    expect(getLiveVoiceState("chat-a").status).toBe("muted");
  });

  it("flushes mid-turn assistant text before the next tool", async () => {
    const { startLiveChat } = await import("./liveSpeakService");
    startLiveChat("chat-a", { voiceId: "v", speed: 1 });
    eventHandler?.({ type: "text_delta", conv_id: "chat-a", text: "Dog mesh is real-world scale." });
    eventHandler?.({
      type: "tool",
      conv_id: "chat-a",
      tool: { name: "search_assets", arguments: { search: "dog" } },
    });
    expect(enqueueAnswerSpeak).toHaveBeenCalledWith(
      "Dog mesh is real-world scale.",
      expect.objectContaining({ chatId: "chat-a" }),
    );
  });
});
