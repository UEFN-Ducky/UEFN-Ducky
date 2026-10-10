// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";
const state = vi.hoisted(() => ({ listeners: new Set<(event: AgentEvent) => void>(), running: new Set<string>(), open: vi.fn() }));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: (fn: (event: AgentEvent) => void) => { state.listeners.add(fn); return () => state.listeners.delete(fn); } }));
vi.mock("../hooks/useRunningAgents", () => ({ useRunningAgents: () => state.running }));
vi.mock("../navigation/openChatReference", () => ({ requestOpenChatTab: state.open }));
const api = vi.hoisted(() => ({ list_all_conversations: vi.fn(), group_members: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
import { PlanOwnerChips, ownerActivity } from "./PlanOwnerChips";
import { PlanTodoCard } from "./PlanTodoCard";
import { nodeKind } from "../utils/planLock";
import { subscribeLatestChatActivity, requestLatestChatActivity } from "../navigation/latestChatActivity";
import { markChatTurnIdle, markChatTurnRunning } from "../hooks/chatTurnTimer";
function emit(event: AgentEvent) { act(() => { for (const fn of state.listeners) fn(event); }); }
afterEach(() => { cleanup(); state.running.clear(); vi.clearAllMocks(); });
function roster() {
 api.list_all_conversations.mockResolvedValue([{ id: "g", title: "Group C", is_group: true }]);
 api.group_members.mockResolvedValue({ members: [{ member_conv_id: "b", name: "Builder", ducky_style: "classic" }] });
}
describe("plan owners", () => {
 it("shows group and members and opens member at latest activity", async () => {
  roster(); render(<PlanOwnerChips ownerId="g" />);
  await screen.findByText("Builder");
  expect(screen.getByText("Group C")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Builder/ }));
  expect(state.open).toHaveBeenCalledWith("b", "Builder");
  const scroll = vi.fn(); const off = subscribeLatestChatActivity("b", scroll);
  expect(scroll).toHaveBeenCalledOnce(); off();
  fireEvent.click(screen.getByRole("button", { name: /Group C/ }));
  expect(state.open).toHaveBeenCalledWith("g", "Group C");
 });
 it("updates tool, waiting, elapsed time, and idle from chat events", async () => {
  roster(); state.running.add("b"); markChatTurnRunning("b", Date.now() - 72000);
  render(<PlanOwnerChips ownerId="g" />); await screen.findByText("Builder");
  emit({ type: "tool", conv_id: "b", tool: { name: "Bash", arguments: { command: "rg test src" } } });
  expect(screen.getByRole("button", { name: /Builder/ }).textContent).toMatch(/Search.*1m 12s/i);
  emit({ type: "linked_agent", parent_conv_id: "b", child_conv_id: "other", status: "running" });
  expect(screen.getByText(/Waiting for reply/)).toBeTruthy();
  emit({ type: "linked_agent", parent_conv_id: "b", child_conv_id: "other", status: "done" });
  expect(screen.queryByText(/Waiting for reply/)).toBeNull();
  state.running.delete("b"); emit({ type: "agent_stopped", conv_id: "b" });
  expect(screen.getByRole("button", { name: /Builder/ }).textContent).toContain("Idle");
 });
 it("shows inherited active builders without nesting buttons in outline buttons", async () => {
  roster(); state.running.add("b");
  const { container } = render(<PlanTodoCard onSelectNode={() => {}} plan={{ id: "p", chat_id: "c", title: "Plan", overview: "", body_markdown: "", todos: [], nodes: [{ id: "g", content: "Section", status: "pending", assignee: "g", children: [{ id: "s", content: "Task", status: "in_progress" }] }] }} />);
  await waitFor(() => expect(screen.getAllByText("Builder")).toHaveLength(2));
  expect(container.querySelector("button button")).toBeNull();
 });
 it("refreshes owner names after roster changes", async () => {
  roster(); render(<PlanOwnerChips ownerId="g" />); await screen.findByText("Group C");
  api.list_all_conversations.mockResolvedValue([{ id: "g", title: "Renamed group", is_group: true }]);
  emit({ type: "chats_changed" }); await screen.findByText("Renamed group");
 });
 it("renders all four coordinator sections with named groups", async () => {
  api.list_all_conversations.mockResolvedValue(["A", "B", "C", "D"].map(letter => ({ id: letter, title: `Group ${letter}`, is_group: true })));
  api.group_members.mockResolvedValue({ members: [] });
  render(<PlanTodoCard plan={{ id: "four", chat_id: "coordinator", title: "Round", overview: "", body_markdown: "", todos: [], nodes: ["A", "B", "C", "D"].map(letter => ({ id: letter, content: `Section ${letter}`, status: "pending", assignee: letter })) }} />);
  emit({ type: "chats_changed" });
  for (const letter of ["A", "B", "C", "D"]) await screen.findByRole("button", { name: new RegExp(`Group ${letter}`) });
 });
 it("classifies existing steps with children as subplans", () => {
  expect(nodeKind({ id: "a", content: "A", status: "pending", kind: "step", children: [{ id: "b", content: "B", status: "pending" }] })).toBe("subplan");
 });
 it("uses the same activity formatter and ignores unrelated events", () => {
  expect(ownerActivity({ type: "thinking" })).toBe("Thinking");
  expect(ownerActivity({ type: "status", text: "Waiting for reply" })).toBe("Waiting for reply");
  expect(ownerActivity({ type: "usage" })).toBeUndefined();
 });
 it("does not tick while nobody on the plan is running", async () => {
  vi.useFakeTimers();
  const setIntervalSpy = vi.spyOn(window, "setInterval");
  try {
   const ids = Array.from({ length: 40 }, (_, i) => `g${i}`);
   api.list_all_conversations.mockResolvedValue(ids.map(id => ({ id, title: `Group ${id}`, is_group: false })));
   let commits = 0;
   render(<Profiler id="plan" onRender={() => { commits += 1; }}>
    <PlanTodoCard plan={{ id: "big", chat_id: "coordinator", title: "Big", overview: "", body_markdown: "", todos: [], nodes: ids.map(id => ({ id, content: `Section ${id}`, status: "pending", assignee: id })) }} />
   </Profiler>);
   emit({ type: "chats_changed" });
   await act(async () => { await vi.advanceTimersByTimeAsync(0); });
   expect(screen.getByRole("button", { name: /Group g39/ })).toBeTruthy();
   const commitsBefore = commits;
   for (let i = 0; i < 10; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
   expect(commits).toBe(commitsBefore);
   expect(setIntervalSpy.mock.calls.filter(([, ms]) => ms === 1000)).toHaveLength(0);
  } finally {
   setIntervalSpy.mockRestore();
   vi.useRealTimers();
  }
 });
 it("ticks the elapsed time of a running owner", async () => {
  roster(); state.running.add("b"); markChatTurnIdle("b"); markChatTurnRunning("b", Date.now() - 5000);
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
  try {
   render(<PlanOwnerChips ownerId="g" />);
   emit({ type: "chats_changed" });
   await act(async () => { await vi.advanceTimersByTimeAsync(0); });
   expect(screen.getByRole("button", { name: /Builder/ }).textContent).toMatch(/0m 5s/);
   await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
   expect(screen.getByRole("button", { name: /Builder/ }).textContent).toMatch(/0m 8s/);
  } finally {
   vi.useRealTimers();
  }
 });
 it("delivers latest navigation to mounted chats and consumes it once", () => {
  const scroll = vi.fn(); const off = subscribeLatestChatActivity("mounted", scroll);
  requestLatestChatActivity("mounted"); expect(scroll).toHaveBeenCalledOnce(); off();
  const again = vi.fn(); subscribeLatestChatActivity("mounted", again)(); expect(again).not.toHaveBeenCalled();
 });
});
