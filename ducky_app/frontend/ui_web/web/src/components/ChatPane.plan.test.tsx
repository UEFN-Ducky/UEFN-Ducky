// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatPlan, PlanProgress } from "../types/panel";
const api = vi.hoisted(() => ({ get_plan: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => null }));
import { useChatPlan } from "./ChatPane";
import { ChatPlanPopup } from "./ChatPlanPopup";
import { pushLocalAgentEvent } from "../hooks/useAgentEventBus";

const assigned: ChatPlan = {
  id: "plan", chat_id: "coord", title: "Group A", status: "open", todos: [],
  nodes: [{ id: "section", content: "Group A", kind: "subplan", status: "pending", children: [
    { id: "first", content: "Old task", status: "pending", children: [] },
  ] }],
  assigned_from: { chat_id: "coord", node_id: "section", plan_title: "Master", assignee: "group" },
};
const progress: PlanProgress = { total: 2, completed: 0, pending: 2, in_progress: 0, cancelled: 0 };
const changed: ChatPlan = { ...assigned, title: "Renamed group", nodes: [
  { id: "section", content: "Renamed group", kind: "subplan", status: "pending", children: [
    { id: "new", content: "Added task", kind: "subplan", status: "pending", children: [
      { id: "first", content: "Moved finished task", status: "completed", children: [] },
    ] },
  ] },
] };
function Card() {
  const { chatPlan, chatPlanProgress } = useChatPlan("builder");
  return chatPlan ? <ChatPlanPopup plan={chatPlan} progress={chatPlanProgress} /> : null;
}
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  api.get_plan.mockResolvedValue({ ok: true, plan: assigned, progress });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it("refreshes the member card's title, status, added and moved steps from coordinator events", async () => {
  render(<Card />);
  fireEvent.click(await screen.findByTitle("Expand plan"));
  await screen.findByText("Old task");
  api.get_plan.mockResolvedValue({ ok: true, plan: changed, progress: { ...progress, total: 3, completed: 1 } });
  act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord", plan: { ...assigned, title: "Entire master plan" } }));
  await screen.findByText("Added task");
  await screen.findByText("Moved finished task");
  expect(screen.queryByText("Old task")).toBeNull();
  expect(screen.queryByText("Entire master plan")).toBeNull();
  expect(screen.getAllByText("Renamed group").length).toBeGreaterThan(0);
  expect(api.get_plan).toHaveBeenLastCalledWith("builder");
  expect(screen.getByText("1/3")).toBeTruthy();
  const moved = screen.getByText("Moved finished task").closest("[data-plan-node-id]");
  expect(moved?.getAttribute("data-status")).toBe("completed");
  expect(moved?.textContent).toContain("1.1.1");
});

it("refreshes on assignment changes and unsubscribes from the old coordinator", async () => {
  const { result } = renderHook(() => useChatPlan("builder"));
  await waitFor(() => expect(result.current.chatPlan).toEqual(assigned));
  const reassigned = { ...assigned, assigned_from: { ...assigned.assigned_from!, chat_id: "new-coord" } };
  api.get_plan.mockResolvedValue({ plan: reassigned, progress });
  act(() => pushLocalAgentEvent({ type: "plan_assignment_changed", conv_id: "coord" }));
  await waitFor(() => expect(result.current.chatPlan).toEqual(reassigned));
  api.get_plan.mockClear();
  act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord" }));
  await act(async () => { await new Promise(r => setTimeout(r, 50)); });
  expect(api.get_plan).not.toHaveBeenCalled();
  api.get_plan.mockResolvedValue({ plan: null, progress: null });
  act(() => pushLocalAgentEvent({ type: "plan_assignment_changed", conv_id: "builder" }));
  await waitFor(() => expect(result.current.chatPlan).toBeNull());
  expect(result.current.chatPlanProgress).toBeNull();
});

it("keeps own-plan event behavior and ignores another chat's plan", async () => {
  const own = { ...assigned, chat_id: "solo", assigned_from: undefined };
  api.get_plan.mockResolvedValue({ plan: own, progress });
  const { result } = renderHook(() => useChatPlan("solo"));
  await waitFor(() => expect(result.current.chatPlan).toEqual(own));
  const update = { ...own, title: "Own update" };
  act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "solo", plan: update, progress }));
  await waitFor(() => expect(result.current.chatPlan).toEqual(update));
  act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord", plan: assigned }));
  await act(async () => { await new Promise(r => setTimeout(r, 50)); });
  expect(result.current.chatPlan).toEqual(update);
  expect(api.get_plan).toHaveBeenCalledTimes(1);
});

it("discards an older refresh and a previous chat's delayed load", async () => {
  const { result, rerender } = renderHook(({ id }) => useChatPlan(id), { initialProps: { id: "builder" } });
  await waitFor(() => expect(result.current.chatPlan).toEqual(assigned));
  let finish!: (value: unknown) => void;
  api.get_plan.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord" }));
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  api.get_plan.mockResolvedValue({ plan: changed, progress: { ...progress, completed: 1 } });
  act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord" }));
  await waitFor(() => expect(result.current.chatPlan).toEqual(changed));
  await act(async () => finish({ plan: assigned, progress }));
  expect(result.current.chatPlan).toEqual(changed);
  api.get_plan.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  act(() => pushLocalAgentEvent({ type: "plan_updated", conv_id: "coord" }));
  await waitFor(() => expect(api.get_plan).toHaveBeenCalledTimes(4));
  api.get_plan.mockResolvedValue({ plan: null, progress: null });
  rerender({ id: "other" });
  await waitFor(() => expect(result.current.chatPlan).toBeNull());
  await act(async () => finish({ plan: assigned, progress }));
  expect(result.current.chatPlan).toBeNull();
});
