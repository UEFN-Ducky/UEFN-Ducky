// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../types/panel";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const api = {
  load_messages: vi.fn<(chatId: string) => Promise<ChatMessage[]>>(),
  list_running_agents: vi.fn<() => Promise<string[]>>(async () => []),
  report_ui_error: vi.fn<(source: string, message: string) => Promise<{ ok: boolean }>>(async () => ({ ok: true })),
};
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
vi.mock("./useAgentEventBus", () => ({
  useAgentEventSubscription: () => {},
  subscribeAgentEvents: () => () => {},
}));

import { CHAT_LOAD_TIMEOUT_MS } from "./chatRowsFetch";
import { useChatMessages } from "./useChatMessages";

describe("a chat that never loads", () => {
  it("stops spinning, offers Retry and leaves a line in Settings → Errors", async () => {
    vi.useFakeTimers();
    api.load_messages.mockImplementation(() => new Promise<ChatMessage[]>(() => {}));
    const { result } = renderHook(() => useChatMessages("abcdef12-0000-0000-0000-000000000000", true, false));
    expect(result.current.hydrated).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(CHAT_LOAD_TIMEOUT_MS + 10);
    });

    expect(result.current.hydrated).toBe(true);
    expect(result.current.loadError).toBe("This chat is taking too long to load.");
    expect(api.report_ui_error).toHaveBeenCalledWith("chat_timeout.unknown", expect.stringContaining("Chat abcdef12 didn't load"));

    // Retry loads it once the backend answers.
    api.load_messages.mockResolvedValue([{ id: 1, role: "user", text: "hi" }]);
    await act(async () => {
      await result.current.reloadMessages();
    });
    expect(result.current.loadError).toBe("");
    expect(result.current.messages).toHaveLength(1);
  });
});

describe("a slow chat load", () => {
  it("tags chat_slow with the chat's size", async () => {
    const rows: ChatMessage[] = Array.from({ length: 600 }, (_, i) => ({ id: i * 1000, role: "user", text: `m${i}` }));
    api.load_messages.mockResolvedValue(rows);
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => (now += 7_000));
    const { result } = renderHook(() => useChatMessages("5100aaaa-0000-0000-0000-000000000000", true, false));
    await vi.waitFor(() => expect(result.current.hydrated).toBe(true));
    expect(api.report_ui_error).toHaveBeenCalledWith("chat_slow.long", expect.stringContaining("(600 messages)"));
  });
});
