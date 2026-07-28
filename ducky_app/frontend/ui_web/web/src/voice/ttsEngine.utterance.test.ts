import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Multi-speaker utterance queue: order + barge-in clear.
 * speechSynthesis is stubbed so the test stays headless.
 */

vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => null,
}));

vi.mock("./audioSettings", () => ({
  getAudioSettings: () => ({ ttsVolume: 1, audioMuted: false }),
  effectivePlaybackVolume: (v: number) => v,
}));

type SynthStub = {
  speak: ReturnType<typeof vi.fn>;
  cancel: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  resume: ReturnType<typeof vi.fn>;
  getVoices: () => SpeechSynthesisVoice[];
  speaking: boolean;
};

function installSynth(): SynthStub {
  const stub: SynthStub = {
    speak: vi.fn((u: SpeechSynthesisUtterance) => {
      stub.speaking = true;
      queueMicrotask(() => {
        stub.speaking = false;
        u.onend?.(new Event("end") as SpeechSynthesisEvent);
      });
    }),
    cancel: vi.fn(() => {
      stub.speaking = false;
    }),
    pause: vi.fn(),
    resume: vi.fn(),
    getVoices: () => [],
    speaking: false,
  };
  (globalThis as unknown as { speechSynthesis: SynthStub }).speechSynthesis = stub;
  (globalThis as unknown as { SpeechSynthesisUtterance: typeof SpeechSynthesisUtterance }).SpeechSynthesisUtterance =
    function SpeechSynthesisUtterance(this: SpeechSynthesisUtterance, text?: string) {
      this.text = text || "";
    } as unknown as typeof SpeechSynthesisUtterance;
  return stub;
}

describe("ttsEngine utterance queue", () => {
  beforeEach(() => {
    installSynth();
    vi.resetModules();
  });

  it("plays enqueued utterances in order with distinct voices", async () => {
    const { ttsEngine } = await import("./ttsEngine");
    const heard: string[] = [];
    ttsEngine.onUtteranceChange((info) => {
      if (info.speaker) heard.push(info.speaker);
    });

    ttsEngine.enqueueUtterance("hello from A", "builtin:A", "Ducky A");
    ttsEngine.enqueueUtterance("hello from B", "builtin:B", "Ducky B");

    await vi.waitFor(() => {
      expect(ttsEngine.getUtteranceQueueLength()).toBe(0);
      expect(ttsEngine.isSpeaking()).toBe(false);
    });

    expect(heard).toContain("Ducky A");
    expect(heard).toContain("Ducky B");
    expect(heard.indexOf("Ducky A")).toBeLessThan(heard.indexOf("Ducky B"));
  });

  it("cancel clears the multi-speaker queue (barge-in)", async () => {
    const { ttsEngine } = await import("./ttsEngine");
    ttsEngine.enqueueUtterance("one", "builtin:A", "A");
    ttsEngine.enqueueUtterance("two", "builtin:B", "B");
    ttsEngine.enqueueUtterance("three", "builtin:C", "C");
    expect(ttsEngine.getUtteranceQueueLength()).toBeGreaterThan(0);
    ttsEngine.cancel();
    expect(ttsEngine.getUtteranceQueueLength()).toBe(0);
    expect(ttsEngine.getCurrentSpeaker()).toBe("");
    expect(ttsEngine.isSpeaking()).toBe(false);
  });
});
