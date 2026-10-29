// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Callbacks = {
  onStateChange: (s: string) => void;
  onInterim: (t: string) => void;
  onFinal: (t: string) => void;
};
const mock = vi.hoisted(() => ({
  cb: null as null | Callbacks,
  abort: vi.fn(),
  stop: vi.fn(async () => {}),
}));

vi.mock("./transcriptionSession", () => ({
  prewarmSpeech: () => {},
  createBatchTranscriptionSession: () => ({
    start: async (cb: Callbacks) => {
      mock.cb = cb;
      cb.onStateChange("listening");
    },
    stop: mock.stop,
    abort: mock.abort,
  }),
}));
vi.mock("../ui-targets/registry", () => ({ useUiTarget: () => undefined }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => null, isRemote: () => false }));
vi.mock("./liveSpeakService", () => ({ interruptLiveSpeak: () => {}, startLiveChat: () => {}, stopLiveChat: () => {} }));
vi.mock("./useLiveChatPresence", () => ({ useIsLiveChat: () => false }));
vi.mock("./useLiveVoiceMode", () => ({
  useLiveVoiceMode: () => ({
    skip: () => {}, back: () => {}, newest: () => {}, interrupt: () => {}, retry: () => {},
    hasPrev: false, hasNext: false, hasNewer: false,
  }),
}));
vi.mock("./VoiceOverlay", () => ({ VoiceOverlay: () => null }));
vi.mock("./audioSettings", () => ({ saveAudioSettings: async () => {} }));
vi.mock("./TtsReadAlong", () => ({ mapReadAlong: () => null }));
vi.mock("./ttsEngine", () => ({ ttsEngine: { onStateChange: () => () => {}, getState: () => "idle" } }));
vi.mock("./spokenReplyGate", () => ({ userAwaitsReply: () => false }));
vi.mock("./voiceSettings", () => {
  const s = { enabled: false, liveAutoSend: false, processTalk: 0, spokenStyle: "off" };
  return {
    getVoiceSettings: () => s,
    loadVoiceSettings: async () => s,
    resolveSpeed: () => 1,
    resolveVoiceId: () => "",
    saveVoiceSettings: async () => {},
    subscribeVoiceSettings: () => () => {},
  };
});

import { VoiceControls } from "./VoiceControls";

/** Composer stand-in: Send aborts dictation first, then clears the box (like ChatComposer). */
function Harness({ chatId }: { chatId: string }) {
  const [text, setText] = useState("");
  const abortRef = useRef<(() => void) | null>(null);
  return (
    <>
      <output data-testid="box">{text}</output>
      <button type="button" onClick={() => { abortRef.current?.(); setText(""); }}>Send</button>
      <VoiceControls
        chatId={chatId}
        inputText={text}
        setInputText={setText}
        onSend={() => {}}
        onLiveChange={() => {}}
        abortDictationRef={abortRef}
      />
    </>
  );
}

const box = () => screen.getByTestId("box").textContent;

async function dictate(words: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Dictate" }));
  });
  act(() => mock.cb!.onFinal(words));
}

beforeEach(() => {
  mock.cb = null;
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("dictation stops on send, chat switch and hidden window", () => {
  it("Send aborts and late words never come back", async () => {
    render(<Harness chatId="a" />);
    await dictate("hello there");
    expect(box()).toBe("hello there");
    act(() => fireEvent.click(screen.getByRole("button", { name: "Send" })));
    expect(mock.abort).toHaveBeenCalledOnce();
    expect(box()).toBe("");
    act(() => {
      mock.cb!.onInterim("late");
      mock.cb!.onFinal("late words");
    });
    expect(box()).toBe("");
    expect(screen.getByRole("button", { name: "Dictate" })).toBeTruthy();
  });

  it("switching chats aborts without writing into the next chat", async () => {
    const { rerender } = render(<Harness chatId="a" />);
    await dictate("first chat");
    rerender(<Harness chatId="b" />);
    expect(mock.abort).toHaveBeenCalledOnce();
    act(() => mock.cb!.onFinal("stray"));
    expect(box()).toBe("first chat");
  });

  it("hiding the window aborts", async () => {
    render(<Harness chatId="a" />);
    await dictate("hi");
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    expect(mock.abort).toHaveBeenCalledOnce();
    act(() => mock.cb!.onFinal("more"));
    expect(box()).toBe("hi");
  });

  it("Stop keeps the dictated words", async () => {
    render(<Harness chatId="a" />);
    await dictate("keep me");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }));
    });
    expect(mock.stop).toHaveBeenCalledOnce();
    expect(mock.abort).not.toHaveBeenCalled();
    expect(box()).toBe("keep me");
  });
});
