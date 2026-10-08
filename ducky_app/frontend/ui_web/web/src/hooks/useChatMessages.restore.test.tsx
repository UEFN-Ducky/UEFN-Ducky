// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent, ChatMessage } from "../types/panel";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const api = {
  load_messages: vi.fn<(chatId: string) => Promise<ChatMessage[]>>(),
  list_running_agents: vi.fn<() => Promise<string[]>>(),
};
const bus = vi.hoisted(() => ({ listeners: new Set<(event: AgentEvent) => void>() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
vi.mock("./useAgentEventBus", () => ({
  useAgentEventSubscription: () => {},
  subscribeAgentEvents: (listener: (event: AgentEvent) => void) => {
    bus.listeners.add(listener);
    return () => bus.listeners.delete(listener);
  },
}));

import { mergeCommitted } from "./chatRun/chatRunReducer";
import { setCachedChatMessages } from "./chatMessagesCache";
import { useChatMessages } from "./useChatMessages";

/** What the agent event bus delivers while no pane for the chat is mounted. */
function emit(event: AgentEvent) {
  for (const listener of bus.listeners) listener(event);
}

/** The view a chat tab left behind when it went to the background mid-run. */
function cacheMidRun(chatId: string) {
  setCachedChatMessages(chatId, {
    messages: [
      { id: 1, role: "user", text: "build the demo" },
      { id: "opt-4", role: "tool", text: "", tool: { name: "save_workflow", arguments: {} } },
    ],
    streamBuffer: "Checking the final wiring",
    streamThinking: "",
    optimisticRunning: true,
    hasNewBelow: false,
    isAtBottom: true,
    activeRunId: "run-1",
    stoppedRun: false,
  });
}

const finalRows: ChatMessage[] = [
  { id: 1, role: "user", text: "build the demo" },
  { id: 2, role: "tool", text: "", tool: { name: "save_workflow", arguments: {} } },
  { id: 3, role: "assistant", text: "The demo workflow is saved and wired." },
];

describe("a chat tab reopened after it was in the background", () => {
  beforeEach(() => {
    api.load_messages.mockReset();
    api.list_running_agents.mockReset();
  });

  it("shows the finished run at once when the run ended while the tab was away", async () => {
    cacheMidRun("chat-done");
    api.list_running_agents.mockResolvedValue([]);
    api.load_messages.mockResolvedValue(finalRows);

    const { result } = renderHook(() => useChatMessages("chat-done", true, false));

    // Before: stuck on "Running tools…" until a 15 s reconcile timer fired.
    await waitFor(() => expect(result.current.agentRunning).toBe(false), { timeout: 1000 });
    await waitFor(() => expect(result.current.messages).toEqual(finalRows), { timeout: 1000 });
    expect(result.current.streamBuffer).toBe("");
  });

  it("stays live when the run is still going", async () => {
    cacheMidRun("chat-live");
    api.list_running_agents.mockResolvedValue(["chat-live"]);
    api.load_messages.mockResolvedValue(finalRows.slice(0, 2));

    const { result } = renderHook(() => useChatMessages("chat-live", true, true));

    await waitFor(() => expect(api.load_messages).toHaveBeenCalled(), { timeout: 1000 });
    expect(result.current.agentRunning).toBe(true);
  });
});

describe("a chat tab that goes to the background mid-run", () => {
  beforeEach(() => {
    api.load_messages.mockReset();
    api.list_running_agents.mockReset();
  });

  it("keeps following the run, so coming back shows where it is", async () => {
    const history: ChatMessage[] = [{ id: 1, role: "user", text: "hi" }, { id: 2, role: "assistant", text: "hey" }];
    api.load_messages.mockResolvedValue(history);
    api.list_running_agents.mockResolvedValue(["chat-bg"]);
    const first = renderHook(() => useChatMessages("chat-bg", true, true));
    await waitFor(() => expect(first.result.current.messages).toEqual(history));
    act(() => {
      first.result.current.appendUserMessage("build the demo");
      first.result.current.setActiveRunId("run-9");
    });
    first.unmount(); // another tab in front

    emit({ type: "text_delta", text: "Opening the file.", conv_id: "chat-bg", run_id: "run-9" });
    emit({ type: "tool", text: "", tool: { name: "find_workflows", arguments: {} }, conv_id: "chat-bg", run_id: "run-9" });

    // The backend's checkpoint of this turn (incomplete) must not be merged in.
    api.load_messages.mockResolvedValue([
      ...history,
      { id: 3, role: "user", text: "build the demo" },
      { id: 4, role: "assistant", text: "Opening the file.", incomplete: true },
    ]);
    const again = renderHook(() => useChatMessages("chat-bg", true, true));
    await waitFor(() => expect(api.load_messages).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(again.result.current.messages.map((m) => m.text)).toEqual([
      "hi", "hey", "build the demo", "Opening the file.", "",
    ]));
    expect(again.result.current.messages.some((m) => m.incomplete)).toBe(false);
    expect(again.result.current.agentRunning).toBe(true);
  });

  it("shows the finished reply when the run ended while the tab was away", async () => {
    api.load_messages.mockResolvedValue([]);
    api.list_running_agents.mockResolvedValue(["chat-end"]);
    const first = renderHook(() => useChatMessages("chat-end", true, true));
    await waitFor(() => expect(api.load_messages).toHaveBeenCalled());
    act(() => {
      first.result.current.appendUserMessage("go");
      first.result.current.setActiveRunId("run-2");
    });
    first.unmount();
    emit({ type: "text_delta", text: "Done.", conv_id: "chat-end", run_id: "run-2" });
    emit({ type: "assistant_done", conv_id: "chat-end", run_id: "run-2" });
    emit({ type: "agent_stopped", reason: "done", conv_id: "chat-end", run_id: "run-2" });

    const final: ChatMessage[] = [{ id: 1, role: "user", text: "go" }, { id: 2, role: "assistant", text: "Done." }];
    api.load_messages.mockResolvedValue(final);
    api.list_running_agents.mockResolvedValue([]);
    const again = renderHook(() => useChatMessages("chat-end", true, false));
    expect(again.result.current.agentRunning).toBe(false); // already idle on the first frame
    await waitFor(() => expect(again.result.current.messages).toEqual(final));
  });
});

describe("open chat visibility", () => {
  beforeEach(() => { api.load_messages.mockReset(); api.list_running_agents.mockReset(); });

  it("reuses loaded messages on rapid switches and refreshes an older snapshot in the background", async () => {
    let now = 100000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    api.load_messages.mockResolvedValue(finalRows);
    const view = renderHook(({ visible }) => useChatMessages("kept-open", visible, false), { initialProps: { visible: true } });
    await waitFor(() => expect(view.result.current.hydrated).toBe(true));
    const messages = view.result.current.messages;
    for (let n = 0; n < 4; n++) {
      view.rerender({ visible: false });
      view.rerender({ visible: true });
    }
    expect(api.load_messages).toHaveBeenCalledTimes(1);
    expect(view.result.current.messages).toBe(messages);
    now += 61000;
    view.rerender({ visible: false });
    view.rerender({ visible: true });
    expect(view.result.current.hydrated).toBe(true);
    await waitFor(() => expect(api.load_messages).toHaveBeenCalledTimes(2));
  });

  it("does not refresh a scrolled-up reader merely because the pane reappears", async () => {
    let now = 100000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    api.load_messages.mockResolvedValue(finalRows);
    const view = renderHook(({ visible }) => useChatMessages("reading-open", visible, false), { initialProps: { visible: true } });
    await waitFor(() => expect(view.result.current.hydrated).toBe(true));
    act(() => view.result.current.onAtBottomChange(false));
    view.rerender({ visible: false });
    now += 61000;
    view.rerender({ visible: true });
    expect(api.load_messages).toHaveBeenCalledTimes(1);
    expect(view.result.current.isAtBottom).toBe(false);
  });
});

describe("mergeCommitted while a run is live", () => {
  const history: ChatMessage[] = [{ id: 1, role: "user", text: "hi" }, { id: 2, role: "assistant", text: "hey" }];
  const live: ChatMessage[] = [
    ...history,
    { id: "opt-0", role: "user", text: "Inspect Tycoony" },
    { id: "opt-1", role: "assistant", text: "I'll open the file." },
    { id: "opt-2", role: "tool", text: "", tool: { name: "workspace_list_verse_errors", arguments: {} } },
  ];

  it("keeps the user's message above the agent's output", () => {
    const checkpoint: ChatMessage[] = [
      ...history,
      { id: 3, role: "user", text: "Inspect Tycoony" },
      { id: 4, role: "assistant", text: "I'll open the file.", incomplete: true },
    ];
    const merged = mergeCommitted(live, checkpoint);
    expect(merged.map((m) => m.text)).toEqual(["hi", "hey", "Inspect Tycoony", "I'll open the file.", ""]);
    expect(merged.some((m) => m.incomplete)).toBe(false);
  });

  it("does not stack another copy of the checkpoint on every reload", () => {
    let view = live;
    for (let n = 0; n < 3; n++) {
      const growing: ChatMessage[] = [
        ...history,
        { id: 3, role: "user", text: "Inspect Tycoony" },
        ...Array.from({ length: n + 1 }, (_, k) => ({ id: 4 + k, role: "assistant" as const, text: `step ${k}`, incomplete: true })),
      ];
      view = mergeCommitted(view, growing);
    }
    expect(view.map((m) => m.text)).toEqual(["hi", "hey", "Inspect Tycoony", "I'll open the file.", ""]);
  });

  it("puts a background-started run's prompt in order after the history", () => {
    const shown: ChatMessage[] = [...history, { id: "opt-5", role: "tool", text: "", tool: { name: "ducky_get_status", arguments: {} } }];
    const backend: ChatMessage[] = [...history, { id: 3, role: "user", text: "Hub: check the island" }, { id: 4, role: "assistant", text: "", incomplete: true }];
    expect(mergeCommitted(shown, backend).map((m) => m.text)).toEqual(["hi", "hey", "Hub: check the island", ""]);
  });
});
