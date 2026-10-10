// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";

vi.mock("./usePanelApi", () => ({ getApi: () => ({ list_running_agents: async () => [] }), isRemote: () => false }));
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => null }));

import { getChatTurnTimer } from "./chatTurnTimer";
import { pushLocalAgentEvent } from "./useAgentEventBus";
import { useRunningAgents } from "./useRunningAgents";

it("times a turn from when its events arrived, not when a minimized window delivered them", async () => {
  // A hidden window queues events and hands start and stop over in one frame:
  // the clock used to read "Took 0ms" for a 53 second turn.
  renderHook(() => useRunningAgents());
  pushLocalAgentEvent({ type: "tool", conv_id: "codex-chat", received_at: 1_000 });
  pushLocalAgentEvent({ type: "agent_stopped", conv_id: "codex-chat", reason: "done", received_at: 54_000 });
  await vi.waitFor(() => expect(getChatTurnTimer("codex-chat")?.endedAt).toBe(54_000));
  expect(getChatTurnTimer("codex-chat")).toEqual({ startedAt: 1_000, endedAt: 54_000 });
});
