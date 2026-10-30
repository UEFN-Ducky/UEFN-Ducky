// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";
const state = vi.hoisted(() => ({ listeners: new Set<(event: AgentEvent) => void>(), running: new Set<string>(), open: vi.fn() }));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: (fn: (event: AgentEvent) => void) => { state.listeners.add(fn); return () => state.listeners.delete(fn); } }));
vi.mock("../hooks/useRunningAgents", () => ({ useRunningAgents: () => state.running }));
vi.mock("../navigation/openChatReference", () => ({ requestOpenChatTab: state.open }));
const api = vi.hoisted(() => ({ list_all_conversations: vi.fn(), group_members: vi.fn(), list_agent_profiles: vi.fn(), plan_set_planned_assignee: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
import { PlanAssignChip, PlanOwnerChips, menuPlacement, ownerActivity } from "./PlanOwnerChips";
import { PlanTodoCard } from "./PlanTodoCard";
import { nodeKind } from "../utils/planLock";
import { subscribeLatestChatActivity, requestLatestChatActivity } from "../navigation/latestChatActivity";
import { markChatTurnIdle, markChatTurnRunning } from "../hooks/chatTurnTimer";
function emit(event: AgentEvent) { act(() => { for (const fn of state.listeners) fn(event); }); }
afterEach(() => { cleanup(); state.running.clear(); vi.clearAllMocks(); });
function roster() {
 api.list_all_conversations.mockResolvedValue([{ id: "g", title: "Group C", is_group: true }]);
 api.group_members.mockResolvedValue({ ok: true, members: [{ member_conv_id: "b", name: "Builder", ducky_style: "classic" }] });
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
 it("updates tool, waiting and idle from chat events", async () => {
  roster(); state.running.add("b"); markChatTurnRunning("b", Date.now() - 72000);
  render(<PlanOwnerChips ownerId="g" />); await screen.findByText("Builder");
  emit({ type: "tool", conv_id: "b", tool: { name: "Bash", arguments: { command: "rg test src" } } });
  expect(screen.getByRole("button", { name: /Builder/ }).textContent).toMatch(/Search/i);
  emit({ type: "linked_agent", parent_conv_id: "b", child_conv_id: "other", status: "running" });
  await screen.findByText(/Waiting for reply/);
  emit({ type: "linked_agent", parent_conv_id: "b", child_conv_id: "other", status: "done" });
  // The chip re-renders after the event; checking at once failed most runs (release test run).
  await waitFor(() => expect(screen.queryByText(/Waiting for reply/)).toBeNull());
  state.running.delete("b"); emit({ type: "agent_stopped", conv_id: "b" });
  await waitFor(() => expect(screen.getByRole("button", { name: /Builder/ }).textContent).toContain("Idle"));
 });
 it("steps inherit their section's agent: one chip on the section, none on the step, no nested buttons", async () => {
  roster(); state.running.add("b");
  const { container } = render(<PlanTodoCard onSelectNode={() => {}} plan={{ id: "p", chat_id: "c", title: "Plan", overview: "", body_markdown: "", todos: [], nodes: [{ id: "g", content: "Section", status: "pending", assignee: "g", children: [{ id: "s", content: "Task", status: "in_progress" }] }] }} />);
  await waitFor(() => expect(screen.getAllByText("Builder")).toHaveLength(1));
  expect(container.querySelector('[data-plan-node-id="s"] + .plan-owner-chips')).toBeNull();
  expect(container.querySelector("button button")).toBeNull();
 });
 it("a 3-deep sub plan offers Assign; the picker lists members then profiles and sets planned_assignee", async () => {
  api.list_all_conversations.mockResolvedValue([{ id: "team", title: "Team", is_group: true, group_members: [{ member_conv_id: "lead" }, { member_conv_id: "dev", name: "Tool server dev" }] }]);
  api.list_agent_profiles.mockResolvedValue({ profiles: [{ id: "verse-dev", name: "Verse Developer", ducky_style: "hacker" }] });
  api.plan_set_planned_assignee.mockResolvedValue({ ok: true });
  const step = { id: "c1", content: "Step", status: "pending" as const };
  const { container } = render(<PlanTodoCard plan={{ id: "p", chat_id: "lead", title: "Plan", todos: [], nodes: [
   { id: "a", content: "App core", status: "pending", children: [
    { id: "b", content: "Bridge", status: "pending", children: [{ id: "c", content: "HTTP", status: "pending", children: [step] }] },
   ] },
  ] }} />);
  const assign = screen.getAllByRole("button", { name: "Assign" });
  expect(assign).toHaveLength(3);
  expect(container.querySelector('[data-plan-node-id="c1"] + .plan-owner-chips')).toBeNull();
  fireEvent.click(assign[2]!);
  await screen.findByRole("menuitem", { name: /Verse Developer/ });
  expect(screen.getAllByRole("menuitem").map(item => item.textContent)).toEqual(["Tool server dev", "Verse Developer"]);
  fireEvent.click(screen.getByRole("menuitem", { name: /Verse Developer/ }));
  await waitFor(() => expect(api.plan_set_planned_assignee).toHaveBeenCalledWith("lead", "c", "verse-dev"));
  expect((await screen.findByRole("button", { name: /Verse Developer.*planned/ })).hasAttribute("disabled")).toBe(false);
  expect(state.open).not.toHaveBeenCalled();
 });
 it("a planned owner shows dimmed as planned, Remove clears it, and a member's read-only view can't change it", async () => {
  api.list_all_conversations.mockResolvedValue([{ id: "dev2", title: "Tool server dev" }]);
  api.list_agent_profiles.mockResolvedValue({ profiles: [] });
  api.plan_set_planned_assignee.mockResolvedValue({ ok: true });
  const nodes = [{ id: "s", content: "Tool server", status: "pending" as const, planned_assignee: "dev2", children: [{ id: "s1", content: "Step", status: "pending" as const }] }];
  const view = render(<PlanTodoCard plan={{ id: "p", chat_id: "lead", title: "Plan", todos: [], nodes }} />);
  const chip = await screen.findByRole("button", { name: /Tool server dev.*planned/ });
  fireEvent.click(chip);
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove" }));
  await waitFor(() => expect(api.plan_set_planned_assignee).toHaveBeenCalledWith("lead", "s", ""));
  await screen.findByRole("button", { name: "Assign" });
  view.unmount();
  render(<PlanTodoCard plan={{ id: "p", chat_id: "builder", title: "Plan", todos: [], nodes, assigned_from: { chat_id: "lead", node_id: "s", plan_title: "Plan", assignee: "g" } }} />);
  expect((await screen.findByRole("button", { name: /Tool server dev.*planned/ })).hasAttribute("disabled")).toBe(true);
 });
 it("chips of the same group share the roster store's one group_members call", async () => {
  api.list_all_conversations.mockResolvedValue([{ id: "g2", title: "Group Two", is_group: true }]);
  api.group_members.mockResolvedValue({ ok: true, members: [{ member_conv_id: "m", name: "Member" }] });
  render(<><PlanOwnerChips ownerId="g2" /><PlanOwnerChips ownerId="g2" /></>);
  // Drops the cached chat directory without a roster push.
  emit({ type: "plan_assignment_changed", conv_id: "elsewhere" });
  await waitFor(() => expect(screen.getAllByText("Member")).toHaveLength(2));
  expect(api.group_members).toHaveBeenCalledTimes(1);
  render(<PlanOwnerChips ownerId="g2" />);
  await waitFor(() => expect(screen.getAllByText("Member")).toHaveLength(3));
  expect(api.group_members).toHaveBeenCalledTimes(1);
 });
 it("places the owner menu above a low chip and keeps it inside the window", () => {
  expect(menuPlacement({ top: 100, bottom: 120, left: 40 }, 800, 1200)).toMatchObject({ top: 120, bottom: "auto", maxHeight: 260 });
  const low = menuPlacement({ top: 740, bottom: 760, left: 40 }, 800, 1200);
  expect(low).toMatchObject({ top: "auto", bottom: 60, maxHeight: 260 });
  // A chip at the right edge pulls the menu in; a short window caps the height so it scrolls.
  expect(menuPlacement({ top: 100, bottom: 120, left: 1150 }, 800, 1200).left).toBe(1200 - 200 - 8);
  expect(menuPlacement({ top: 150, bottom: 170, left: 0 }, 300, 1200)).toMatchObject({ top: "auto", maxHeight: 142, overflowY: "auto" });
 });
 it("a chip near the bottom of the window opens its menu upward, on the page body", async () => {
  api.list_all_conversations.mockResolvedValue([]);
  api.list_agent_profiles.mockResolvedValue({ profiles: [{ id: "dev", name: "Developer" }] });
  const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ top: window.innerHeight - 30, bottom: window.innerHeight - 10, left: 20, right: 80, width: 60, height: 20, x: 20, y: window.innerHeight - 30, toJSON: () => ({}) } as DOMRect);
  try {
   const { container } = render(<div style={{ height: 120, overflow: "hidden" }}><PlanAssignChip chatId="lead" nodeId="n" /></div>);
   fireEvent.click(screen.getByRole("button", { name: "Assign" }));
   const menu = await screen.findByRole("menu");
   expect(menu.dataset.placement).toBe("above");
   expect(menu.style.position).toBe("fixed");
   expect(menu.style.bottom).toBe("30px");
   expect(container.contains(menu)).toBe(false);
   await screen.findByRole("menuitem", { name: /Developer/ });
   fireEvent.keyDown(document, { key: "Escape" });
   await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  } finally {
   rect.mockRestore();
  }
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
 it("a running owner shows what it does, never a clock of time spent", async () => {
  roster(); state.running.add("b"); markChatTurnIdle("b"); markChatTurnRunning("b", Date.now() - 5000);
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
  try {
   render(<PlanOwnerChips ownerId="g" />);
   emit({ type: "chats_changed" });
   await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
   const text = screen.getByRole("button", { name: /Builder/ }).textContent || "";
   expect(text).toContain("Working");
   expect(text).not.toMatch(/\d+m \d+s/);
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
