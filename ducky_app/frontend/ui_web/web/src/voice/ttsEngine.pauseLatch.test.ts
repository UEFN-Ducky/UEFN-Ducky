import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => null,
}));

vi.mock("./audioSettings", () => ({
  getAudioSettings: () => ({ ttsVolume: 1, audioMuted: false }),
  effectivePlaybackVolume: (v: number) => v,
  applyOutputDevice: async () => undefined,
}));

type SynthStub = {
  speak: ReturnType<typeof vi.fn>;
  cancel: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  resume: ReturnType<typeof vi.fn>;
  getVoices: () => SpeechSynthesisVoice[];
  speaking: boolean;
  paused: boolean;
};

function installSynth(): SynthStub {
  let pending: SpeechSynthesisUtterance | null = null;
  const stub: SynthStub = {
    speak: vi.fn((u: SpeechSynthesisUtterance) => {
      // Reproduce Chromium sticky-pause: new utterances start paused.
      pending = u;
      stub.speaking = true;
      stub.paused = true;
      queueMicrotask(() => {
        // Engine should clear the latch via resume().
        if (pending === u && !stub.paused) {
          stub.speaking = false;
          pending = null;
          u.onend?.(new Event("end") as SpeechSynthesisEvent);
        }
      });
    }),
    cancel: vi.fn(() => {
      stub.speaking = false;
      pending = null;
      // Sticky latch stays true until resume() — Chromium bug.
      stub.paused = true;
    }),
    pause: vi.fn(() => {
      stub.paused = true;
    }),
    resume: vi.fn(() => {
      stub.paused = false;
      const u = pending;
      if (u && stub.speaking) {
        queueMicrotask(() => {
          if (pending !== u) return;
          stub.speaking = false;
          pending = null;
          u.onend?.(new Event("end") as SpeechSynthesisEvent);
        });
      }
    }),
    getVoices: () => [],
    speaking: false,
    paused: false,
  };
  (globalThis as unknown as { speechSynthesis: SynthStub }).speechSynthesis = stub;
  (globalThis as unknown as { SpeechSynthesisUtterance: typeof SpeechSynthesisUtterance }).SpeechSynthesisUtterance =
    function SpeechSynthesisUtterance(this: SpeechSynthesisUtterance, text?: string) {
      this.text = text || "";
    } as unknown as typeof SpeechSynthesisUtterance;
  return stub;
}

describe("ttsEngine Chromium pause latch", () => {
  beforeEach(() => {
    installSynth();
    vi.resetModules();
  });

  it("resumes sticky-paused utterances so speak does not freeze on the first word", async () => {
    const stub = installSynth();
    vi.resetModules();
    const { ttsEngine } = await import("./ttsEngine");

    // Prior pause() leaves Chromium latched.
    stub.paused = true;
    ttsEngine.speak("Creating a followable plan.");

    await vi.waitFor(() => {
      expect(stub.resume).toHaveBeenCalled();
      expect(stub.paused).toBe(false);
    });

    await vi.waitFor(() => {
      expect(ttsEngine.getState()).toBe("idle");
    });
  });
});
