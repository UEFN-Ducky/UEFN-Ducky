// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Keep the real subscription, conversation filter and scheduled event delivery.
// Stub only the transport so this rendered test never polls a running app.
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => ({ onEvent: () => {} }) }));
vi.mock("../hooks/perfMonitor", () => ({
  installPerfMonitor: () => {}, noteFrameDelivery: () => {}, notePendingDepth: () => {},
}));
const openPlan = vi.hoisted(() => vi.fn());
vi.mock("../navigation/openPlanTab", () => ({ requestOpenPlanTab: openPlan }));
const api = vi.hoisted(() => ({ get_plan: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
// Always mounted (closed); it needs the ducky catalog provider this test does not set up.
vi.mock("./ducky/DuckyProfileModal", () => ({ DuckyProfileModal: () => null }));

import { PlanPane } from "./PlanPane";
import { pushLocalAgentEvent } from "../hooks/useAgentEventBus";

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
  vi.clearAllMocks();
});

describe("PlanPane for a team member", () => {
  it("discovers, reassigns and clears assignments through member-scoped invalidation", async () => {
    api.get_plan.mockResolvedValue({ ok: true, plan: null, progress: null });
    render(<PlanPane chatId="builder" chatName="Builder" />);
    await screen.findByText("No plan for this chat yet.");
    const emit = async (convId: string) => act(async () => {
      pushLocalAgentEvent({ type: "plan_assignment_changed", conv_id: convId });
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    api.get_plan.mockClear();
    await emit("outsider");
    expect(api.get_plan).not.toHaveBeenCalled();
    api.get_plan.mockResolvedValue({ ok: true, plan: assigned, progress: null });
    await emit("builder");
    await screen.findByText("Open full plan");
    expect(screen.queryByText("Edit")).toBeNull();
    api.get_plan.mockResolvedValue({ ok: true, plan: { ...assigned, title: "New assignment" }, progress: null });
    await emit("builder");
    await screen.findByRole("heading", { name: "New assignment" });
    api.get_plan.mockResolvedValue({ ok: true, plan: null, progress: null });
    await emit("builder");
    await screen.findByText("No plan for this chat yet.");
  });

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
    act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord" }));
    await waitFor(() => expect(api.get_plan).toHaveBeenCalledWith("builder", null));
  });
});
