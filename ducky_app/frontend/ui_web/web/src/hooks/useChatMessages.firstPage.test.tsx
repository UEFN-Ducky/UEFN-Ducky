// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../types/panel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const api = {
  load_messages: vi.fn<(chatId: string, beforeId?: number | null, limit?: number) => Promise<ChatMessage[]>>(),
  list_running_agents: vi.fn<() => Promise<string[]>>(async () => []),
};
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
vi.mock("./useAgentEventBus", () => ({
  useAgentEventSubscription: () => {},
  subscribeAgentEvents: () => () => {},
}));

import { useChatMessages } from "./useChatMessages";

describe("opening a 900-message chat", () => {
  it("opens with the newest 80 rows and pages older ones in 300 at a time on scroll to the top", async () => {
    const all: ChatMessage[] = Array.from({ length: 900 }, (_, k) => ({ id: k * 1000, role: "user", text: `m${k}` }));
    api.load_messages.mockImplementation(async (_id, before, limit) => {
      const end = before == null ? all.length : all.findIndex((m) => m.id === before);
      return limit ? all.slice(Math.max(0, end - limit), end) : all;
    });
    const { result } = renderHook(() => useChatMessages("big-chat", true, false));

    await waitFor(() => expect(result.current.hydrated).toBe(true));
    expect(api.load_messages).toHaveBeenLastCalledWith("big-chat", null, 80);
    expect(result.current.messages).toHaveLength(80);
    expect(result.current.messages[0].id).toBe(820_000);

    await act(async () => {
      await result.current.loadOlder();
    });
    expect(api.load_messages).toHaveBeenLastCalledWith("big-chat", 820_000, 300);
    expect(result.current.messages).toHaveLength(380);
    expect(result.current.messages[0].id).toBe(520_000);

    await act(async () => {
      await result.current.loadOlder();
    });
    expect(result.current.messages).toHaveLength(680);
    await act(async () => {
      await result.current.loadOlder();
    });
    expect(result.current.messages).toHaveLength(900);
    // The start of the chat (row id 0) is shown: nothing more to ask for.
    const calls = api.load_messages.mock.calls.length;
    await act(async () => {
      await result.current.loadOlder();
    });
    expect(api.load_messages).toHaveBeenCalledTimes(calls);
    // Never the whole chat in one reply.
    expect(api.load_messages.mock.calls.every((call) => call[2] === 80 || call[2] === 300)).toBe(true);
  });
});
