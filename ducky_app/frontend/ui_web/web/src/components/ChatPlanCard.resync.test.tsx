// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { AgentEvent } from "../types/panel";

const handlers: ((e: AgentEvent) => void)[] = [];
const api = {
  get_plan: vi.fn(async (_chatId: string): Promise<{ plan: unknown; progress: unknown }> => ({
    plan: { id: "p" },
    progress: { completed: 6, total: 12 },
  })),
};
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../hooks/useAgentEventBus", () => ({
  useAgentEventSubscription: (_id: string, h: (e: AgentEvent) => void) => {
    handlers.push(h);
  },
}));
vi.mock("../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({}) }));

import { REMOTE_RESYNC } from "../remote/protocol";
import { useChatPlan } from "./ChatPlanCard";

afterEach(() => cleanup());

it("the phone's link back after a drop reloads the plan, so steps ticked meanwhile show", async () => {
  // Nov 1 2026: the phone's plan bar sat at 6/12 while the PC's plan had moved on.
  const { result } = renderHook(() => useChatPlan("c1"));
  await act(async () => {});
  expect(result.current.chatPlanProgress).toEqual({ completed: 6, total: 12 });
  api.get_plan.mockResolvedValue({ plan: { id: "p" }, progress: { completed: 12, total: 12 } });
  await act(async () => {
    for (const h of handlers.splice(0)) h({ type: REMOTE_RESYNC } as unknown as AgentEvent);
  });
  expect(result.current.chatPlanProgress).toEqual({ completed: 12, total: 12 });
});
