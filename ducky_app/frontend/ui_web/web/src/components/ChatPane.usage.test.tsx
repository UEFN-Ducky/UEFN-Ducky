// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AgentEvent, ChatTab } from "../types/panel";

const calls = vi.hoisted(() => [] as string[]);
const api = vi.hoisted(() => new Proxy({}, {
  get: (_target, prop) => (prop === "then" ? undefined : () => {
    calls.push(String(prop));
    return Promise.resolve([]);
  }),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, isPanelApiReady: () => true }));
vi.mock("./VirtualChatMessageList", () => ({ VirtualChatMessageList: () => null }));
vi.mock("../voice/VoiceControls", () => ({ VoiceControls: () => null }));
vi.mock("../voice/VoiceOverlay", () => ({ VoiceOverlay: () => null }));
vi.mock("./ModelSelector", () => ({ ModelSelector: () => null }));
vi.mock("./ModeSelector", () => ({ ModeSelector: () => null }));
vi.mock("./DuckyParade", () => ({ DuckyParadeOverlay: () => null }));
vi.mock("./ElapsedTimer", () => ({ ElapsedTimer: () => null }));

import { ChatPane } from "./ChatPane";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { pushLocalAgentEvent } from "../hooks/useAgentEventBus";

const chat: ChatTab = { id: "usage-chat", name: "Builder", model: "m" };
const count = (name: string) => calls.filter((c) => c === name).length;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  calls.length = 0;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("30 usage events in one second make 1 get_context_usage call and 0 get_settings calls", async () => {
  render(
    <ConfirmModalProvider>
      <ChatPane chat={chat} visible allChats={[chat]} onOpenChat={() => {}} isAgentRunning />
    </ConfirmModalProvider>,
  );
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  calls.length = 0;
  for (let i = 0; i < 30; i++) {
    act(() => pushLocalAgentEvent({ type: "usage", conv_id: chat.id, input_tokens: i } as AgentEvent));
    await act(async () => { await vi.advanceTimersByTimeAsync(16); });
  }
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(count("get_context_usage")).toBe(1);
  expect(count("get_settings")).toBe(0);
});

it("30 tool_done events in one second make 1 get_session_files call", async () => {
  render(
    <ConfirmModalProvider>
      <ChatPane chat={chat} visible allChats={[chat]} onOpenChat={() => {}} isAgentRunning />
    </ConfirmModalProvider>,
  );
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  calls.length = 0;
  for (let i = 0; i < 30; i++) {
    act(() => pushLocalAgentEvent({ type: "tool_done", conv_id: chat.id, tool: { name: "x" } } as AgentEvent));
    await act(async () => { await vi.advanceTimersByTimeAsync(16); });
  }
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(count("get_session_files")).toBe(1);
});
