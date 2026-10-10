// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Keep the real subscription, conversation filter and scheduled event delivery.
// Stub only the transport so this rendered test never polls a running app.
const transport = vi.hoisted(() => ({ onEvent: () => {}, onStatus: vi.fn(() => () => {}) }));
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => transport }));
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
  it("resolves archived owner events instead of replacing an assigned view", async () => {
    api.get_plan.mockResolvedValue({ ok: true, plan: assigned });
    render(<PlanPane chatId="builder" />);
    await screen.findByText("Open full plan");
    expect(screen.getByRole("button", { name: /group-a/ })).toBeTruthy();
    api.get_plan.mockClear();
    await act(async () => {
      pushLocalAgentEvent({ type: "plan_updated", conv_id: "builder", plan: {
        ...assigned, chat_id: "builder", assigned_from: undefined, title: "Archived personal", status: "archived",
      } });
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(screen.queryByRole("heading", { name: "Archived personal" })).toBeNull();
    expect(screen.getByText("Open full plan")).toBeTruthy();
    expect(api.get_plan).toHaveBeenCalledWith("builder", null);
  });

  it("keeps the latest assignment when refreshes finish out of order", async () => {
    api.get_plan.mockResolvedValue({ ok: true, plan: assigned });
    render(<PlanPane chatId="builder" />);
    await screen.findByText("Open full plan");
    let stale!: (value: unknown) => void;
    api.get_plan.mockImplementationOnce(() => new Promise(resolve => { stale = resolve; }));
    await act(async () => {
      pushLocalAgentEvent({ type: "plan_assignment_changed", conv_id: "builder" });
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    api.get_plan.mockResolvedValue({ ok: true, plan: null });
    await act(async () => {
      pushLocalAgentEvent({ type: "plan_assignment_changed", conv_id: "builder" });
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    await screen.findByText("No plan for this chat yet.");
    await act(async () => stale({ ok: true, plan: assigned }));
    expect(screen.getByText("No plan for this chat yet.")).toBeTruthy();
  });

  it("ignores a previous tab's delayed load and old unscoped events", async () => {
    let finish!: (value: unknown) => void;
    api.get_plan.mockImplementation((id: string) => id === "old"
      ? new Promise(resolve => { finish = resolve; })
      : Promise.resolve({ ok: true, plan: { ...assigned, title: "Selected" } }));
    const view = render(<PlanPane chatId="old" />);
    view.rerender(<PlanPane chatId="builder" />);
    await screen.findByRole("heading", { name: "Selected" });
    await act(async () => finish({ ok: true, plan: { ...assigned, title: "Stale" } }));
    await act(async () => {
      pushLocalAgentEvent({ type: "plan_updated", plan: { ...assigned, title: "Unscoped" } });
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(screen.getByRole("heading", { name: "Selected" })).toBeTruthy();
  });

  it("refreshes every bound view on canonical changes and reconnect", async () => {
    api.get_plan.mockResolvedValue({ ok: true, plan: assigned });
    render(<><PlanPane chatId="builder" /><PlanPane chatId="builder2" /></>);
    await waitFor(() => expect(screen.getAllByText("Open full plan")).toHaveLength(2));
    api.get_plan.mockClear();
    api.get_plan.mockResolvedValue({ ok: true, plan: {
      ...assigned, nodes: [{ ...assigned.nodes[0], content: "Canonical changed step" }],
    } });
    act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord" }));
    await waitFor(() => {
      expect(api.get_plan).toHaveBeenCalledWith("builder", null);
      expect(api.get_plan).toHaveBeenCalledWith("builder2", null);
    });
    await waitFor(() => expect(screen.getAllByText("Canonical changed step")).toHaveLength(2));
    api.get_plan.mockClear();
    api.get_plan.mockResolvedValue({ ok: true, plan: { ...assigned, title: "Reconnected" } });
    act(() => {
      for (const [listener] of transport.onStatus.mock.calls as unknown as Array<[(s: { state: string }) => void]>) {
        listener({ state: "live" });
      }
    });
    await waitFor(() => expect(screen.getAllByRole("heading", { name: "Reconnected" })).toHaveLength(2));
    api.get_plan.mockResolvedValue({ ok: true, plan: null });
    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(screen.getAllByText("No plan for this chat yet.")).toHaveLength(2));
  });

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
