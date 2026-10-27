// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";
const mocks = vi.hoisted(() => ({ listener: null as null | ((event: AgentEvent) => void), open: vi.fn(), push: vi.fn(),
  api: { list_all_conversations: vi.fn(), list_running_agents: vi.fn(), load_messages: vi.fn(), terminal_list: vi.fn(), cancel_agent: vi.fn() } }));
vi.mock("./usePanelApi", () => ({ getApi: () => mocks.api }));
vi.mock("./useAgentEventBus", () => ({
  subscribeAgentEvents: (fn: (event: AgentEvent) => void) => { mocks.listener = fn; return () => { mocks.listener = null; }; },
  pushLocalAgentEvent: mocks.push,
}));
vi.mock("../navigation/openChatReference", () => ({ requestOpenChatTab: mocks.open }));
vi.mock("./workflowRunsByChat", () => ({ subscribeWorkflowEvents: () => () => {}, refreshWorkflowRuns: vi.fn(), stopWorkflowRun: vi.fn() }));
// The tray's command cards listen on the bus too; this file's bus mock holds one listener.
vi.mock("../terminal/terminalApprovals", () => {
  const none: unknown[] = [];
  return { subscribeTerminalApprovals: () => () => {}, listTerminalApprovals: () => none,
    terminalApprovalChoices: () => none, answerTerminalApproval: vi.fn() };
});
import { applyAgentBackgroundEvent, openAgentBackgroundJob, subscribeAgentBackgroundActivity } from "./agentBackgroundActivity";
import { _resetBackgroundActivityForTests, getBackgroundJobs, countWorkingBackgroundJobs, upsertBackgroundJob } from "./backgroundActivity";
import { requestToolActivity } from "../navigation/toolActivity";
import { BackgroundActivityDropdown } from "../components/BackgroundActivityDropdown";
import { AgentActivityGroup } from "../components/AgentActivityGroup";
const start = (conv = "other", id = "call-1"): AgentEvent => ({ type: "tool", conv_id: conv, run_id: "run-1",
  tool: { id, name: "exec_command", arguments: { cmd: "py -3 -m pytest" }, status: "pending" } });
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(100_000);
  mocks.api.list_all_conversations.mockResolvedValue([{ id: "other", title: "Coordinator" }]);
  mocks.api.list_running_agents.mockResolvedValue([]);
  mocks.api.load_messages.mockResolvedValue([]);
});
afterEach(() => { cleanup(); _resetBackgroundActivityForTests(); vi.useRealTimers(); vi.clearAllMocks(); });

it("subscribes globally: one row per running chat, saying what it does now", async () => {
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  act(() => { mocks.listener!(start()); upsertBackgroundJob({ id: "workflow:test", title: "Workflow", phase: "working" }); });
  expect(screen.getByTitle("2 running in the background")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Background activity"));
  expect(screen.getByText("Coordinator is working")).toBeTruthy();
  // The tool call itself stays in the chat; the tray names the step.
  expect(screen.queryByText("py -3 -m pytest")).toBeNull();
  act(() => vi.advanceTimersByTime(65_000));
  expect(screen.getAllByText(/Running tests.*1m/).length).toBeGreaterThan(0);
  act(() => mocks.listener!({ ...start(), type: "tool_done", success: false, tool: { ...start().tool!, status: "error", result: "Exit code: 2" } }));
  expect(screen.getByTitle("2 running in the background")).toBeTruthy();
  expect(screen.getAllByText(/^Working/).length).toBeGreaterThan(0);
  act(() => mocks.listener!({ type: "agent_stopped", conv_id: "other", run_id: "run-1", reason: "done" }));
  expect(screen.getByTitle("1 running in the background")).toBeTruthy();
  fireEvent.click(screen.getByText("Clear finished"));
  expect(getBackgroundJobs()).toHaveLength(1);
});

it("shows how many run as a corner badge, 9+ past nine", async () => {
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  const badge = () => screen.getByLabelText("Background activity").querySelector(".terminal-header-badge")?.textContent;
  act(() => { upsertBackgroundJob({ id: "workflow:a", title: "A", phase: "working" }); });
  expect(badge()).toBe("1");
  act(() => { for (let i = 0; i < 10; i++) upsertBackgroundJob({ id: `workflow:${i}`, title: `W${i}`, phase: "working" }); });
  expect(badge()).toBe("9+");
  expect(screen.queryByText(/running$/)).toBeNull();
});

it("keeps one row across parallel calls, ignores late stops from another run, and closes it", () => {
  applyAgentBackgroundEvent(start(), "Coordinator", 1000);
  applyAgentBackgroundEvent(start("other", "call-2"), "Coordinator", 1100);
  applyAgentBackgroundEvent(start(), "Coordinator", 1200);
  expect(getBackgroundJobs()).toHaveLength(1);
  expect(getBackgroundJobs()[0].startedAt).toBe(1000);
  expect(getBackgroundJobs().some((j) => j.toolId)).toBe(false);
  applyAgentBackgroundEvent({ type: "agent_stopped", conv_id: "other", run_id: "older", reason: "done" });
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(1);
  applyAgentBackgroundEvent({ type: "agent_stopped", conv_id: "other", run_id: "run-1", reason: "cancelled" });
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(0);
  expect(getBackgroundJobs()[0].detail).toBe("Stopped");
});

it("does not touch the store for every streamed word", () => {
  applyAgentBackgroundEvent({ type: "text_delta", conv_id: "other", run_id: "run-1", text: "a" }, "Coordinator", 1000);
  const first = getBackgroundJobs()[0];
  applyAgentBackgroundEvent({ type: "text_delta", conv_id: "other", run_id: "run-1", text: "b" }, "Coordinator", 2000);
  expect(getBackgroundJobs()[0]).toBe(first);
  expect(first.detail).toBe("Writing response");
});

it("opens the chat of a row, including before that chat mounts", async () => {
  applyAgentBackgroundEvent(start(), "Coordinator");
  await openAgentBackgroundJob(getBackgroundJobs()[0]);
  expect(mocks.open).toHaveBeenCalledWith("other");
});

it("recovers a running turn without an open chat, and expires finished agent jobs", async () => {
  mocks.api.list_running_agents.mockResolvedValue(["other"]);
  mocks.api.load_messages.mockResolvedValue([{ id: 1, role: "tool", text: "", tool: start().tool }]);
  const stop = subscribeAgentBackgroundActivity();
  await act(async () => {});
  expect(getBackgroundJobs()).toHaveLength(1);
  expect(getBackgroundJobs()[0].detail).toBe("Running tests");
  applyAgentBackgroundEvent({ type: "agent_stopped", conv_id: "other", reason: "done" });
  vi.advanceTimersByTime(65_000);
  expect(getBackgroundJobs()).toEqual([]);
  stop();
  expect(mocks.listener).toBeNull();
});

it("drops tool rows an older build left in the tray", () => {
  upsertBackgroundJob({ id: "tool:other:call-1", source: "agent", convId: "other", toolId: "call-1", title: "uefn/workspace_find", phase: "done" });
  const stop = subscribeAgentBackgroundActivity();
  expect(getBackgroundJobs()).toEqual([]);
  stop();
});

it("opens a folded tool group and card, updates elapsed time, and reveals finished output", () => {
  const intent = { id: "row", role: "tool" as const, text: "", tool: { ...start().tool!, startedAt: Date.now() } };
  const props = { convId: "folded", items: [{ kind: "tool" as const, id: "row", intent, result: null }] };
  const view = render(<AgentActivityGroup {...props} />);
  expect(view.container.querySelector(".tool-execution-card-wrap")).toBeNull();
  act(() => requestToolActivity("folded", "call-1"));
  expect(view.container.querySelector(".tool-execution-card-body")).not.toBeNull();
  act(() => vi.advanceTimersByTime(65_000));
  expect(view.container.textContent).toMatch(/1m/);
  view.rerender(<AgentActivityGroup {...props} items={[{ ...props.items[0], result: { id: "done", role: "success", text: "", tool: { ...intent.tool, status: "success", result: "All tests passed" } } }]} />);
  expect(view.container.textContent).toContain("All tests passed");
});


it("pressing a running row opens its chat", async () => {
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  act(() => mocks.listener!(start("click-chat", "click-tool")));
  fireEvent.click(screen.getByLabelText("Background activity"));
  fireEvent.click(screen.getByText("click-chat is working"));
  expect(mocks.open).toHaveBeenCalledWith("click-chat");
});

it("counts an archived team's running member once, names it with its group, and opens it", async () => {
  mocks.api.list_all_conversations.mockResolvedValue([
    { id: "hub", title: "Live checker", is_group: true, folder_id: "archive" },
    { id: "seat", title: "Ship fixer", folder_id: "archive", parent_conv_id: "hub" },
  ]);
  mocks.api.list_running_agents.mockResolvedValue(["seat"]);
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  expect(screen.getByTitle("1 running in the background")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Background activity"));
  const rows = screen.getAllByText(/ is working$/);
  expect(rows.map((row) => row.textContent)).toEqual(["Live checker · Ship fixer (archived) is working"]);
  fireEvent.click(rows[0]);
  expect(mocks.open).toHaveBeenCalledWith("seat");
});

it("does not evict a running turn when many other jobs arrive", () => {
  applyAgentBackgroundEvent(start(), "Coordinator");
  for (let i = 0; i < 80; i++) upsertBackgroundJob({ id: `history:${i}`, phase: "done" });
  expect(getBackgroundJobs().find((j) => j.id === "agent:other")?.phase).toBe("working");
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(1);
});

it("closes a row the backend no longer runs, keeps one it does, and drops rows of deleted chats", async () => {
  mocks.api.list_all_conversations.mockResolvedValue([{ id: "other", title: "Coordinator" }, { id: "team", title: "Team test r8" }]);
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  // A group's side-chat note streams text and never sends agent_stopped.
  act(() => {
    mocks.listener!({ type: "text_delta", conv_id: "team", text: "(Side chat) note" });
    mocks.listener!(start());
  });
  expect(screen.getByTitle("2 running in the background")).toBeTruthy();
  mocks.api.list_running_agents.mockResolvedValue(["other"]);
  await act(async () => { vi.advanceTimersByTime(15_000); });
  await act(async () => {});
  expect(getBackgroundJobs().find((j) => j.convId === "team")?.phase).toBe("done");
  expect(screen.getByTitle("1 running in the background")).toBeTruthy();
  mocks.api.list_all_conversations.mockResolvedValue([{ id: "other", title: "Coordinator" }]);
  await act(async () => { mocks.listener!({ type: "chats_changed" } as AgentEvent); });
  await act(async () => {});
  expect(getBackgroundJobs().some((j) => j.convId === "team")).toBe(false);
  expect(getBackgroundJobs().filter((j) => j.convId === "other")).toHaveLength(1);
});

it("stops a chat's turn from the tray", async () => {
  mocks.api.cancel_agent.mockResolvedValue(true);
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  act(() => mocks.listener!(start()));
  fireEvent.click(screen.getByLabelText("Background activity"));
  await act(async () => { fireEvent.click(screen.getAllByText("Stop")[0]); });
  expect(mocks.api.cancel_agent).toHaveBeenCalledWith("other");
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(0);
  expect(screen.getByText("Coordinator stopped")).toBeTruthy();
});

it("offers Hide only for a row that cannot be stopped", async () => {
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  act(() => { upsertBackgroundJob({ id: "plugin:job", title: "Plugin import", phase: "working" }); });
  fireEvent.click(screen.getByLabelText("Background activity"));
  fireEvent.click(screen.getByLabelText("Hide"));
  expect(getBackgroundJobs()).toHaveLength(0);
});
