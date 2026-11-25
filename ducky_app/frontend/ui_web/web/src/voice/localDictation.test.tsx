// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { createBatchTranscriptionSession, localWhisperUsable } from "./transcriptionSession";
import { useDictation } from "./useDictation";

const mock = vi.hoisted(() => ({
  installed: false,
  frame: null as ((input: Float32Array, rate: number) => void) | null,
  microphone: vi.fn(),
  job: vi.fn(),
  stop: vi.fn(),
  final: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ voice_whisper_installed: async () => mock.installed }),
  isRemote: () => false,
}));
vi.mock("../hooks/bridgeJobAsync", () => ({ runBridgeJob: mock.job }));
vi.mock("./micPermission", () => ({ requestMicAccess: mock.microphone }));
vi.mock("./pcmCapture", async (original) => ({
  ...await original<typeof import("./pcmCapture")>(),
  startPcmCapture: async (_stream: unknown, onFrame: typeof mock.frame) => {
    mock.frame = onFrame;
    return { sampleRate: 48_000, stop: mock.stop };
  },
}));

function Dictate() {
  const dictation = useDictation({ onInterim: () => {}, onFinal: mock.final });
  return <>
    <button onClick={dictation.toggle}>Dictate</button>
    <output>{dictation.status}</output>
    {dictation.notice?.message}
  </>;
}
function renderDictate() {
  render(<ConfirmModalProvider><Dictate /></ConfirmModalProvider>);
}
async function click(label: RegExp) {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: label })); });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
  mock.installed = false;
  mock.frame = null;
  mock.job.mockImplementation(async (method: string) => {
    if (method === "voice_whisper_install") mock.installed = true;
    return { ok: true, text: "Bonjour" };
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps Linux desktop dictation on its system backend", () => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (X11; Linux x86_64)");
  expect(localWhisperUsable()).toBe(false);
});

it("offers installation on click, Later keeps the mic closed and the next click offers it again", async () => {
  renderDictate();
  expect(screen.queryByRole("dialog")).toBeNull();
  await click(/^Dictate$/);
  expect(screen.getByText("Install offline dictation?")).toBeTruthy();
  await click(/^Later/);
  expect(mock.job).not.toHaveBeenCalled();
  expect(mock.microphone).not.toHaveBeenCalled();
  await click(/^Dictate$/);
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it("installs once, activates the mic and transcribes the final phrase on Stop", async () => {
  renderDictate();
  await click(/^Dictate$/);
  await click(/^Install and dictate/);
  expect(mock.job).toHaveBeenCalledTimes(1);
  expect(mock.microphone).toHaveBeenCalledOnce();
  mock.frame!(new Float32Array(48_000).fill(0.2), 48_000);
  await click(/^Dictate$/);
  await waitFor(() => expect(mock.final).toHaveBeenCalledWith("Bonjour"));
  expect(mock.job.mock.calls[1][0]).toBe("voice_whisper_transcribe");
  await click(/^Dictate$/);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(mock.microphone).toHaveBeenCalledTimes(2);
  expect(mock.job).toHaveBeenCalledTimes(2);
});

it("transcribes continuous speech within the native bridge limit and drains the remainder on Stop", async () => {
  mock.installed = true;
  const session = createBatchTranscriptionSession();
  await session.start({ onFinal: mock.final });
  const frame = new Float32Array(4096).fill(0.2);
  for (let i = 0; i < 265; i++) mock.frame!(frame, 48_000);
  await waitFor(() => expect(mock.final).toHaveBeenCalledTimes(1));
  await session.stop();
  expect(mock.final).toHaveBeenCalledTimes(2);
  for (const [name, args] of mock.job.mock.calls) {
    expect(name).toBe("voice_whisper_transcribe");
    expect(JSON.stringify(args).length).toBeLessThan(1024 * 1024 - 1024);
  }
});

it("keeps a failed installation retryable without opening the microphone", async () => {
  mock.job.mockResolvedValueOnce({ ok: false, error: "Download failed" });
  renderDictate();
  await click(/^Dictate$/);
  await click(/^Install and dictate/);
  expect(screen.getByText("Download failed")).toBeTruthy();
  expect(mock.microphone).not.toHaveBeenCalled();
  await click(/^Dictate$/);
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it("does not open the microphone if the chat closes while the model downloads", async () => {
  let finish!: () => void;
  mock.job.mockImplementation(() => new Promise(resolve => { finish = () => resolve({ ok: true }); }));
  render(<ConfirmModalProvider><div /></ConfirmModalProvider>);
  const session = createBatchTranscriptionSession();
  let started!: Promise<void>;
  await act(async () => { started = session.start(); });
  await click(/^Install and dictate/);
  session.abort();
  finish();
  await started;
  expect(mock.microphone).not.toHaveBeenCalled();
});
