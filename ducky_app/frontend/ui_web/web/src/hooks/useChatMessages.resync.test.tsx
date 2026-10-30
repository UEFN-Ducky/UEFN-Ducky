// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent, ChatMessage } from "../types/panel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const api = {
  load_messages: vi.fn<(chatId: string) => Promise<ChatMessage[]>>(async () => []),
  list_running_agents: vi.fn<() => Promise<string[]>>(async () => []),
  report_ui_error: vi.fn(async () => ({ ok: true })),
};
let handler: ((e: AgentEvent) => void) | null = null;
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
vi.mock("./useAgentEventBus", () => ({
  useAgentEventSubscription: (_id: string, h: (e: AgentEvent) => void) => {
    handler = h;
  },
  subscribeAgentEvents: () => () => {},
}));

import { REMOTE_RESYNC } from "../remote/protocol";
import { useChatMessages } from "./useChatMessages";

describe("the phone's link comes back after a drop", () => {
  it("pulls the chat again, so a reply written meanwhile shows", async () => {
    const { result } = renderHook(() => useChatMessages("c1", true, false));
    await act(async () => {
      await Promise.resolve();
    });
    const loadsBefore = api.load_messages.mock.calls.length;
    api.load_messages.mockResolvedValue([
      { id: 1, role: "user", text: "hi" },
      { id: 2, role: "assistant", text: "done while you slept" },
    ]);
    await act(async () => {
      handler?.({ type: REMOTE_RESYNC } as unknown as AgentEvent);
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    });
    expect(api.load_messages.mock.calls.length).toBe(loadsBefore + 1);
    expect(result.current.messages.map((m) => m.text)).toContain("done while you slept");
  });
});
