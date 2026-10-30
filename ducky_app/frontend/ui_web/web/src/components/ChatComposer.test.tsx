// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatTab } from "../types/panel";

// Every panel call answers with an empty list: enough for the composer to settle.
const calls = vi.hoisted(() => [] as { method: string; args: unknown[] }[]);
const api = vi.hoisted(() => new Proxy({}, {
  get: (_target, prop) => (prop === "then" ? undefined : (...args: unknown[]) => {
    calls.push({ method: String(prop), args });
    return Promise.resolve([]);
  }),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, isPanelApiReady: () => true }));
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => null }));
vi.mock("../voice/VoiceControls", () => ({ VoiceControls: () => null }));
vi.mock("../voice/VoiceOverlay", () => ({ VoiceOverlay: () => null }));
vi.mock("./ModelSelector", () => ({ ModelSelector: () => null }));

import { ChatComposer, composerSend, noteQueuedSend, type ChatComposerProps, type ComposerSendContext } from "./ChatComposer";
import { _resetChatReferenceStoreForTests } from "./chatReferenceStore";
import { _resetAskUserForTests, runAskUser } from "../ask-user/runAskUser";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";

const chat: ChatTab = { id: "composer-chat", name: "Builder" };
const noop = () => {};

function Composer({ onSend }: { onSend: (text?: string) => void }) {
  const [inputText, setInputText] = useState("hello");
  const [caret, setCaret] = useState(5);
  const textareaRef = useRef<HTMLDivElement>(null);
  const props = {
    chat, allChats: [chat], visible: true, isPopup: false, paneHost: null, textareaRef,
    inputText, setInputText, caret, setCaret, onHistoryKey: () => false,
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
    sessionFiles: [], contextPanelOpen: false, onToggleContextPanel: noop, onCloseContextPanel: noop,
    onContextChanged: noop, onOpenFile: noop, onOpenChat: noop, groupMemberCount: 0,
    agentRunning: false, noModelsAvailable: false, modelsUnavailable: false, streamBuffer: "",
    hasContent: true, canQueue: false, canSend: true, onSend, onStop: noop,
  } as unknown as ChatComposerProps;
  return <ChatComposer {...props} />;
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  calls.length = 0;
  _resetChatReferenceStoreForTests();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); _resetAskUserForTests(); });

const CATALOG = ["list_all_conversations", "list_agent_profiles", "get_skill_info", "list_mcp_servers",
  "list_recent_projects", "list_project_file_paths", "list_workflows"];

function sendCtx(text: string, over: Partial<ComposerSendContext> = {}): ComposerSendContext {
  return {
    chatId: chat.id, inputText: text, availableCommands: [], setSlashStatus: noop, setInputText: vi.fn(),
    runSlashCommand: noop, showDefaultModelCta: false, hasLlmProviders: true, pickModelFirst: false,
    hasContent: true, sendAfterPickRef: { current: false }, adoptDefaultModel: async () => true,
    openModelPicker: noop, toApiAttachments: () => [], clearAttachments: noop, agentRunning: true,
    canQueue: true, canCompose: true, canSend: true, agentMode: "agent", selectedModel: "m",
    codingAgent: "ducky", dispatchSend: vi.fn(), ...over,
  } as unknown as ComposerSendContext;
}

it("renders the draft and sends from the Send button", () => {
  const onSend = vi.fn();
  render(<ConfirmModalProvider><Composer onSend={onSend} /></ConfirmModalProvider>);
  expect(screen.getByRole("textbox").textContent).toBe("hello");
  fireEvent.click(screen.getByTitle("Send"));
  expect(onSend).toHaveBeenCalledTimes(1);
});

it("mounting the composer (a chat switch) fetches no @ / slash catalog", () => {
  render(<ConfirmModalProvider><Composer onSend={vi.fn()} /></ConfirmModalProvider>);
  expect(calls.filter((call) => CATALOG.includes(call.method))).toEqual([]);
});

it("Send with an open question answers it and never steers", async () => {
  const asked = runAskUser([{ id: "path", prompt: "Which?", options: [{ id: "b", label: "B" }] }], "", chat.id);
  const ctx = sendCtx("use option B");
  composerSend(ctx);
  await expect(asked).resolves.toMatchObject({ ok: true, free_text: "use option B" });
  expect(calls.some((call) => call.method === "steer_message")).toBe(false);
  expect(ctx.dispatchSend).not.toHaveBeenCalled();
  expect(ctx.setInputText).toHaveBeenCalledWith("");
});

it("a queued send reply clears the box and shows its note for a few seconds; a run_id reply shows none", () => {
  vi.useFakeTimers();
  const note = "Sent — the agent takes it at its next step";
  // The pane's dispatchSend: send_message's reply goes to noteQueuedSend.
  const reply = { current: { ok: true, queued: true, note } as unknown };
  const dispatchSend = vi.fn(() => {
    if (!noteQueuedSend(chat.id, reply.current)) { /* run_id: the pane tracks the run as before */ }
  });
  const ctx = sendCtx("one more thing", { agentRunning: false, dispatchSend });
  render(<ConfirmModalProvider><Composer onSend={() => composerSend(ctx)} /></ConfirmModalProvider>);
  fireEvent.click(screen.getByTitle("Send"));
  expect(ctx.setInputText).toHaveBeenCalledWith("");
  expect(screen.getByText(note)).toBeTruthy();
  expect(screen.queryByText(/already running/i)).toBeNull();
  act(() => { vi.advanceTimersByTime(5000); });
  expect(screen.queryByText(note)).toBeNull();

  reply.current = { run_id: "r1" };
  fireEvent.click(screen.getByTitle("Send"));
  expect(noteQueuedSend(chat.id, reply.current)).toBe(false);
  expect(screen.queryByText(note)).toBeNull();
  vi.useRealTimers();
});

it("Send with no open question steers the running turn as before", () => {
  composerSend(sendCtx("keep going"));
  const steers = calls.filter((call) => call.method === "steer_message");
  expect(steers).toHaveLength(1);
  expect(steers[0].args[2]).toBe("keep going");
});
