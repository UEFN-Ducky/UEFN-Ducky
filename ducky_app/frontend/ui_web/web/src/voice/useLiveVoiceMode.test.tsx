// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getLiveVoiceState, setLiveVoiceChat } from "./liveChats";

const mock = vi.hoisted(() => ({
  start: vi.fn(), abort: vi.fn(), handlers: {} as Record<string, (...args: any[]) => void>,
  tts: null as null | ((s: string) => void),
}));
vi.mock("./transcriptionSession", () => ({
  prewarmSpeech: vi.fn(),
  createStreamingTranscriptionSession: () => ({
    start: async (h: typeof mock.handlers) => { mock.handlers = h; await mock.start(); },
    abort: mock.abort,
  }),
}));
vi.mock("./liveSpeakService", () => ({
  claimMic: () => true, releaseMic: vi.fn(), interruptLiveSpeak: vi.fn(),
  isLiveChat: () => true, updateLiveChatVoice: vi.fn(),
}));
vi.mock("./liveSpeakQueue", () => ({
  getCurrentSpokenLine: () => null, getLiveSpeakTransport: () => ({}),
  liveSpeakQueueLength: () => 0, speakNewest: vi.fn(), speakNext: vi.fn(), speakPrev: vi.fn(),
  subscribeLiveSpeakTransport: () => () => {},
}));
vi.mock("./ttsEngine", () => ({
  ttsEngine: {
    isSpeaking: () => false, setVoice: vi.fn(), setRate: vi.fn(),
    onStateChange: (f: typeof mock.tts) => { mock.tts = f; return () => {}; },
  },
}));
vi.mock("./voiceSettings", () => ({
  getVoiceSettings: () => ({ sttProvider: "", defaultVoice: "" }),
  resolveVoiceId: () => "", resolveSpeed: () => 1, subscribeVoiceSettings: () => () => {},
}));
import { useLiveVoiceMode } from "./useLiveVoiceMode";

const props = { enabled: true, chatId: "live", muted: false, agentRunning: false,
  onInterim: vi.fn(), onTranscript: vi.fn() };

beforeEach(() => {
  vi.useFakeTimers();
  mock.start.mockReset().mockResolvedValue(undefined);
  mock.abort.mockReset();
  setLiveVoiceChat("live", true);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("live mic recovery", () => {
  it("clears reconnect notice when the mic recovers and when muted", async () => {
    const { rerender } = renderHook((p) => useLiveVoiceMode(p), { initialProps: props });
    await act(async () => {});
    act(() => mock.handlers.onError({ message: "Network blip" }));
    expect(getLiveVoiceState("live").notice).toContain("Reconnecting");
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(getLiveVoiceState("live")).toMatchObject({ status: "listening", notice: "", error: "" });
    act(() => mock.handlers.onError({ message: "Another blip" }));
    rerender({ ...props, muted: true });
    expect(getLiveVoiceState("live")).toMatchObject({ status: "muted", notice: "" });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(mock.start).toHaveBeenCalledTimes(2);
  });

  it("stops retrying and retains the error while the agent thinks or talks", async () => {
    mock.start.mockRejectedValue(new Error("Windows speech stopped."));
    const { rerender } = renderHook((p) => useLiveVoiceMode(p), { initialProps: props });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(mock.start).toHaveBeenCalledTimes(4);
    expect(getLiveVoiceState("live")).toMatchObject({ status: "error", notice: "", error: "Windows speech stopped." });
    rerender({ ...props, agentRunning: true });
    act(() => mock.tts?.("idle"));
    expect(getLiveVoiceState("live")).toMatchObject({ status: "error", error: "Windows speech stopped." });
  });

  it("shows fixable privacy errors immediately and retry recovers", async () => {
    const { result } = renderHook(() => useLiveVoiceMode(props));
    await act(async () => {});
    act(() => mock.handlers.onError({ message: "Enable Windows speech", action: "speech_privacy" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(mock.start).toHaveBeenCalledTimes(1);
    expect(getLiveVoiceState("live")).toMatchObject({ status: "error", errorAction: "speech_privacy", notice: "" });
    await act(async () => result.current.retry());
    expect(getLiveVoiceState("live")).toMatchObject({ status: "listening", error: "", errorAction: undefined });
  });
});
