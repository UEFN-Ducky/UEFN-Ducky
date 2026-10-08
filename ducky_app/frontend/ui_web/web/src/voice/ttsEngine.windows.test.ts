// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const mock = vi.hoisted(() => ({
  result: {} as Record<string, unknown>, start: vi.fn(), poll: vi.fn(), output: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({
    voice_win_tts_start: mock.start,
    voice_win_tts_poll: mock.poll,
    voice_win_tts_voices_start: async () => ({ ok: true, job_id: "voices" }),
  }),
}));
vi.mock("./audioSettings", () => ({
  getAudioSettings: () => ({ ttsVolume: 1, audioMuted: false }),
  effectivePlaybackVolume: (v: number) => v, applyOutputDevice: mock.output,
}));
let audios: { play: ReturnType<typeof vi.fn>; pause: ReturnType<typeof vi.fn>; onended?: () => void; src: string }[] = [];
beforeEach(() => {
  vi.resetModules();
  Object.assign(window, { pywebview: {} });
  vi.stubGlobal("speechSynthesis", undefined);
  mock.output.mockReset().mockResolvedValue(undefined);
  mock.start.mockReset().mockResolvedValue({ ok: true, job_id: "speech" });
  mock.poll.mockReset().mockImplementation(async () => mock.result);
  mock.result = { ok: true, audio_base64: btoa("wave"), mime: "audio/wav" };
  audios = [];
  vi.stubGlobal("Audio", class {
    src: string; play = vi.fn(async () => {}); pause = vi.fn();
    constructor(src: string) { this.src = src; audios.push(this); }
  });
  URL.createObjectURL = vi.fn(() => "blob:voice");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { vi.unstubAllGlobals(); delete window.pywebview; });

describe("Windows Speech playback", () => {
  it("plays the default Windows voice without Chromium speech synthesis", async () => {
    const { ttsEngine } = await import("./ttsEngine");
    let ready: (r: { ok: boolean; job_id: string }) => void = () => {};
    mock.start.mockImplementation(() => new Promise((r) => { ready = r; }));
    ttsEngine.speak("Read this reply.");
    await vi.waitFor(() => expect(ttsEngine.getProgress()).toMatchObject({ loading: true, loadingMessage: "Preparing Windows Speech…" }));
    ready({ ok: true, job_id: "speech" });
    await vi.waitFor(() => expect(audios[0]?.play).toHaveBeenCalled());
    expect(mock.start).toHaveBeenCalledWith("Read this reply.", "");
    expect(ttsEngine.getProgress().loading).toBe(false);
    ttsEngine.pause();
    expect(audios[0].pause).toHaveBeenCalled();
    ttsEngine.resume();
    expect(audios[0].play).toHaveBeenCalledTimes(2);
    ttsEngine.cancel();
    await vi.waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:voice"));
    expect(ttsEngine.getState()).toBe("idle");
  });
  it("reports a missing voice instead of silently doing nothing", async () => {
    mock.result = { ok: false, code: "voice_missing", error: "Download a Windows voice." };
    const { ttsEngine } = await import("./ttsEngine");
    ttsEngine.speak("Read this reply.", "builtin:Missing");
    await vi.waitFor(() => expect(ttsEngine.getState()).toBe("idle"));
    expect(ttsEngine.getProgress()).toMatchObject({
      sourceText: "Read this reply.", loading: false, errorCode: "voice_missing", error: "Download a Windows voice.",
    });
    expect(audios).toHaveLength(0);
  });
  it("does not play a cancelled synthesis when its worker finishes", async () => {
    let finish: (r: typeof mock.result) => void = () => {};
    mock.poll.mockImplementation(() => new Promise((r) => { finish = r; }));
    const { ttsEngine } = await import("./ttsEngine");
    ttsEngine.speak("Obsolete reply.");
    await vi.waitFor(() => expect(mock.poll).toHaveBeenCalled());
    ttsEngine.cancel();
    finish(mock.result);
    await Promise.resolve(); await Promise.resolve();
    expect(audios).toHaveLength(0);
    expect(ttsEngine.getState()).toBe("idle");
    expect(ttsEngine.getProgress().loading).toBe(false);
  });
  it("does not start cancelled audio after switching output devices", async () => {
    let ready: () => void = () => {};
    mock.output.mockImplementation(() => new Promise<void>((r) => { ready = r; }));
    const { ttsEngine } = await import("./ttsEngine");
    ttsEngine.speak("Cancelled audio.");
    await vi.waitFor(() => expect(mock.output).toHaveBeenCalled());
    ttsEngine.cancel();
    ready();
    await Promise.resolve(); await Promise.resolve();
    expect(audios[0].play).not.toHaveBeenCalled();
    expect(ttsEngine.getState()).toBe("idle");
  });
  it("lists installed Windows voices even when the WebView has none", async () => {
    mock.result = { ok: true, voices: [{ id: "Microsoft Zira Desktop", label: "Microsoft Zira Desktop", lang: "en-US" }] };
    const { ttsEngine } = await import("./ttsEngine");
    expect(await ttsEngine.whenVoicesReady()).toEqual([
      { id: "builtin:Microsoft Zira Desktop", label: "Microsoft Zira Desktop (en-US)", kind: "builtin" },
    ]);
  });
});
