// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

type Listener = (event: { type: string; conv_id?: string; plan?: unknown }) => void;
const listeners = vi.hoisted(() => [] as Array<{ convId: string; handler: Listener }>);
vi.mock("../hooks/useAgentEventBus", () => ({
  useAgentEventSubscription: (convId: string, handler: Listener) => {
    listeners.push({ convId, handler });
  },
  subscribeAgentEvents: () => () => {},
}));
const openPlan = vi.hoisted(() => vi.fn());
vi.mock("../navigation/openPlanTab", () => ({ requestOpenPlanTab: openPlan }));
const api = vi.hoisted(() => ({ get_plan: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
// Always mounted (closed); it needs the ducky catalog provider this test does not set up.
vi.mock("./ducky/DuckyProfileModal", () => ({ DuckyProfileModal: () => null }));

import { PlanPane } from "./PlanPane";

const assigned = {
  id: "p1",
  chat_id: "coord",
  title: "A Modes",
  overview: "Part of the plan “Repair modes”.",
  status: "open",
  todos: [],
  nodes: [
    {
      id: "a",
      content: "A Modes",
      status: "pending",
      kind: "subplan",
      children: [{ id: "a1", content: "Core", status: "completed", children: [] }],
    },
  ],
  assigned_from: { chat_id: "coord", node_id: "a", plan_title: "Repair modes", assignee: "group-a" },
};

afterEach(() => {
  cleanup();
  listeners.length = 0;
  vi.clearAllMocks();
});

describe("PlanPane for a team member", () => {
  it("shows its group's part of the team plan, read-only, instead of No plan", async () => {
    api.get_plan.mockResolvedValue({
      ok: true,
      plan: assigned,
      progress: { total: 2, completed: 1, cancelled: 0, in_progress: 0, pending: 1 },
    });
    render(<PlanPane chatId="builder" chatName="Builder" />);
    await screen.findByText("Open full plan");
    expect(screen.queryByText("No plan for this chat yet.")).toBeNull();
    expect(screen.queryByText("Edit")).toBeNull();
    expect(screen.queryByText("Send to ducky")).toBeNull();
    expect(screen.queryByText("Make template")).toBeNull();

    fireEvent.click(screen.getByText("Open full plan"));
    expect(openPlan).toHaveBeenCalledWith({ chatId: "coord", title: "Repair modes" });
  });

  it("refreshes when the team plan changes", async () => {
    api.get_plan.mockResolvedValue({ ok: true, plan: assigned, progress: null });
    render(<PlanPane chatId="builder" chatName="Builder" />);
    await screen.findByText("Open full plan");
    api.get_plan.mockClear();
    for (const { convId, handler } of listeners) {
      if (convId === "coord") handler({ type: "plan_updated", conv_id: "coord" });
    }
    await waitFor(() => expect(api.get_plan).toHaveBeenCalledWith("builder", null));
  });
});
