// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatTab } from "../types/panel";

const probe = vi.hoisted(() => ({ composerRenders: 0, voiceText: [] as string[] }));
const api = vi.hoisted(() => new Proxy({}, {
  get: (_t, prop) => (prop === "then" ? undefined : () => Promise.resolve([])),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, isPanelApiReady: () => true }));
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => null }));
// The toolbar's model picker renders whenever the composer does: a render counter.
vi.mock("./ModelSelector", () => ({ ModelSelector: () => { probe.composerRenders += 1; return null; } }));
// VoiceControls' own store read, without its mic/tts machinery.
vi.mock("../voice/VoiceControls", async () => {
  const { useStreamText } = await import("../stores/streamText");
  return {
    VoiceControls: ({ chatId }: { chatId: string }) => {
      probe.voiceText.push(useStreamText(chatId));
      return null;
    },
  };
});
vi.mock("../voice/VoiceOverlay", () => ({ VoiceOverlay: () => null }));

import { ChatComposer, type ChatComposerProps } from "./ChatComposer";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { setStreamText } from "../stores/streamText";

const chat: ChatTab = { id: "stream-chat", name: "Builder" };
const noop = () => {};
let pushFrame: ((text: string) => void) | null = null;

/** A pane stand-in: stream frames re-render it and write the store, like ChatPane. */
function Pane() {
  const [stream, setStream] = useState("");
  pushFrame = (text) => { setStream(text); setStreamText(chat.id, text); };
  const textareaRef = useRef<HTMLDivElement>(null);
  const [props] = useState(() => ({
    chat, allChats: [chat], visible: true, isPopup: false, paneHost: null, textareaRef,
    inputText: "", setInputText: noop, caret: 0, setCaret: noop, onHistoryKey: () => false,
    isDragOver: false, changesOpen: false, onToggleChanges: noop, onCloseChanges: noop,
    liveVoice: false, liveVoiceHandlers: null, onLiveVoiceChange: noop,
    agentMode: "agent", setAgentMode: noop, selectedModel: "m", setSelectedModel: noop,
    codingAgent: "ducky", setCodingAgent: noop, thinkingEffort: "off", setThinkingEffort: noop,
    modelPickerSignal: 0, onPickModel: noop, onModelMetaChange: noop,
    showDefaultModelCta: false, pickModelFirst: false, notConnected: null, llmProviders: [],
    heldForModel: 0, noModelText: "", visionBlocked: false, attachmentError: null, displayModelLabel: "m",
    slashStatus: "", availableCommands: [], runSlashCommand: noop,
    attachments: [], readInParts: false, removeAttachment: noop, retryVideo: noop,
    updateAttachmentImage: noop, addFiles: noop, clearAttachments: noop,
    contextUsage: { used_tokens: 0, context_limit: 1, input_tokens: 0, output_tokens: 0 },
    sessionFiles: [], contextPanelOpen: false, onToggleContextPanel: noop, onCloseContextPanel: noop,
    onContextChanged: noop, onOpenFile: noop, onOpenChat: noop, groupMemberCount: 0,
    agentRunning: true, noModelsAvailable: false, modelsUnavailable: false,
    hasContent: false, canQueue: false, canSend: false, onSend: noop, onStop: noop,
  }) as unknown as ChatComposerProps);
  return <div data-stream={stream}><ChatComposer {...props} /></div>;
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setStreamText(chat.id, ""); });

it("20 stream frames render the composer 0 extra times; voice gets the latest text", () => {
  render(<ConfirmModalProvider><Pane /></ConfirmModalProvider>);
  const before = probe.composerRenders;
  expect(before).toBeGreaterThan(0);
  let text = "";
  for (let i = 0; i < 20; i += 1) {
    text += `tok${i} `;
    act(() => pushFrame!(text));
  }
  expect(probe.composerRenders).toBe(before);
  expect(probe.voiceText.at(-1)).toBe(text);
});
