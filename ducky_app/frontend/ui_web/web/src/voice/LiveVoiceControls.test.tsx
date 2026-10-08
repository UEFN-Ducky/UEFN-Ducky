// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const mock = vi.hoisted(() => ({ progress: { state: "idle", loading: false, spokenText: "", sourceText: "", charIndex: 0, error: "", errorCode: "" }, pause: vi.fn(), resume: vi.fn() }));
vi.mock("./ttsEngine", () => ({ ttsEngine: {
  getProgress: () => mock.progress,
  onProgress: () => () => {}, pause: mock.pause, resume: mock.resume,
} }));
vi.mock("./pluginVoices", () => ({ useTtsVoiceOptions: () => [] }));
vi.mock("./transcriptionSession", () => ({ windowsSpeechUsable: () => true, browserSpeechUsable: () => false }));
vi.mock("../hooks/usePanelApi", () => ({ isRemote: () => false, getApi: () => null }));
vi.mock("./micPermission", () => ({
  listMicDevices: async () => ({ devices: [], defaultLabel: "Headset with a very long device name" }),
  listOutputDevices: async () => ({ devices: [], defaultLabel: "Speakers with a very long device name" }),
}));
import { LiveVoicePickers } from "./LiveVoicePickers";
import { VoiceOverlay } from "./VoiceOverlay";
import { SpeakMessageButton } from "./VoiceControls";
import { patchLiveVoiceState, setLiveVoiceChat } from "./liveChats";
beforeEach(() => { setLiveVoiceChat("voice", true); mock.progress.error = ""; mock.progress.errorCode = ""; mock.progress.sourceText = ""; vi.clearAllMocks(); });
afterEach(cleanup);

describe("live voice controls", () => {
  it("uses icon triggers, keeps accessible labels and offers Windows Speech as default", async () => {
    render(<LiveVoicePickers voiceId="" speed={1} setVoiceId={vi.fn()} setSpeed={vi.fn()} />);
    for (const label of ["Listen backend", "Voice", "Talking speed", "Microphone", "Speakers"]) {
      expect(screen.getByRole("button", { name: label }).textContent).toBe("");
    }
    expect(screen.getByRole("button", { name: "Voice" }).title).toContain("Windows Speech (default)");
    fireEvent.click(screen.getByRole("button", { name: "Voice" }));
    expect(screen.getByRole("radio", { name: "Windows Speech (default)" })).toBeTruthy();
  });
  it("plays the previous available line when idle", () => {
    const back = vi.fn();
    render(<VoiceOverlay chatId="voice" open onClose={vi.fn()} onBack={back} onForward={vi.fn()} onNewest={vi.fn()} showPickers={false} hasPrev />);
    const play = screen.getByTitle("Play") as HTMLButtonElement;
    expect(play.disabled).toBe(false);
    fireEvent.click(play);
    expect(back).toHaveBeenCalledOnce();
  });
  it("shows missing voice errors and the download action beside the reply", () => {
    Object.assign(mock.progress, { sourceText: "Read this reply.", error: "Download a Windows voice.", errorCode: "voice_missing" });
    render(<SpeakMessageButton text="Read this reply." />);
    expect(screen.getByRole("alert").textContent).toContain("Download a Windows voice.");
    expect(screen.getByRole("button", { name: "Download a Windows voice" })).toBeTruthy();
  });
  it("keeps the Windows mic error and retry visible", () => {
    const retry = vi.fn();
    render(<VoiceOverlay chatId="voice" open onClose={vi.fn()} onBack={vi.fn()} onForward={vi.fn()} onNewest={vi.fn()} showPickers={false} onRetry={retry} />);
    act(() => patchLiveVoiceState("voice", { status: "error", error: "Windows speech is off.", errorAction: "speech_privacy" }));
    expect(screen.getByRole("alert").textContent).toContain("Windows speech is off.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
