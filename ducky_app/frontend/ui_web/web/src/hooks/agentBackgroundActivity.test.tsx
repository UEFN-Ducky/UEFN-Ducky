// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";
const mocks = vi.hoisted(() => ({ listener: null as null | ((event: AgentEvent) => void), open: vi.fn(), push: vi.fn(),
  api: { list_all_conversations: vi.fn(), list_running_agents: vi.fn(), load_messages: vi.fn(), terminal_list: vi.fn() } }));
vi.mock("./usePanelApi", () => ({ getApi: () => mocks.api }));
vi.mock("./useAgentEventBus", () => ({
  subscribeAgentEvents: (fn: (event: AgentEvent) => void) => { mocks.listener = fn; return () => { mocks.listener = null; }; },
  pushLocalAgentEvent: mocks.push,
}));
vi.mock("../navigation/openChatReference", () => ({ requestOpenChatTab: mocks.open }));
vi.mock("./workflowRunsByChat", () => ({ subscribeWorkflowEvents: () => () => {}, refreshWorkflowRuns: vi.fn(), stopWorkflowRun: vi.fn() }));
import { applyAgentBackgroundEvent, openAgentBackgroundJob, subscribeAgentBackgroundActivity } from "./agentBackgroundActivity";
import { _resetBackgroundActivityForTests, getBackgroundJobs, countWorkingBackgroundJobs, upsertBackgroundJob } from "./backgroundActivity";
import { useToolActivityTarget, requestToolActivity } from "../navigation/toolActivity";
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

it("subscribes globally, labels another chat's command and counts turns, tools and workflows", async () => {
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  act(() => { mocks.listener!(start()); upsertBackgroundJob({ id: "workflow:test", title: "Workflow", phase: "working" }); });
  expect(screen.getByText("3 running")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Background activity"));
  expect(screen.getByText("Coordinator is working")).toBeTruthy();
  expect(screen.getByText("py -3 -m pytest")).toBeTruthy();
  act(() => vi.advanceTimersByTime(65_000));
  expect(screen.getAllByText(/Coordinator.*1m/).length).toBeGreaterThan(0);
  act(() => mocks.listener!({ ...start(), type: "tool_done", success: false, tool: { ...start().tool!, status: "error", result: "Exit code: 2" } }));
  expect(screen.getByText("2 running")).toBeTruthy();
  expect(screen.getByText(/Failed.*exit 2/)).toBeTruthy();
  act(() => mocks.listener!({ type: "agent_stopped", conv_id: "other", run_id: "run-1", reason: "done" }));
  expect(screen.getByText("1 running")).toBeTruthy();
  fireEvent.click(screen.getByText("Clear finished"));
  expect(getBackgroundJobs()).toHaveLength(1);
});

it("upserts parallel calls by id, ignores late stops from another run, and closes leftovers", () => {
  applyAgentBackgroundEvent(start(), "Coordinator", 1000);
  applyAgentBackgroundEvent(start("other", "call-2"), "Coordinator", 1100);
  applyAgentBackgroundEvent(start(), "Coordinator", 1200);
  expect(getBackgroundJobs().find((j) => j.toolId === "call-1")?.startedAt).toBe(1000);
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(3);
  applyAgentBackgroundEvent({ type: "agent_stopped", conv_id: "other", run_id: "older", reason: "done" });
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(3);
  applyAgentBackgroundEvent({ type: "agent_stopped", conv_id: "other", run_id: "run-1", reason: "cancelled" });
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(0);
  expect(getBackgroundJobs().every((j) => j.detail === "Stopped")).toBe(true);
});

it("opens the requested chat and tool, including before that chat mounts", async () => {
  applyAgentBackgroundEvent(start(), "Coordinator");
  await openAgentBackgroundJob(getBackgroundJobs().find((j) => j.toolId)!);
  expect(mocks.open).toHaveBeenCalledWith("other");
  function Target() { return <span>{useToolActivityTarget("other")?.toolId}</span>; }
  render(<Target />);
  expect(screen.getByText("call-1")).toBeTruthy();
});

it("opens a Ducky terminal session instead of a chat", async () => {
  mocks.api.terminal_list.mockResolvedValue({ sessions: [{ session_id: "term", ws_url: "ws://localhost/term", shell: "powershell", title: "Tests" }] });
  applyAgentBackgroundEvent({ ...start(), tool: { id: "terminal-call", name: "ducky_terminal_run", arguments: { session_id: "term", command: "pytest" } } });
  await openAgentBackgroundJob(getBackgroundJobs().find((j) => j.toolId)!);
  expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({ type: "terminal_open", session_id: "term", activate: true }));
  expect(mocks.open).not.toHaveBeenCalled();
});

it("recovers a running checkpoint without an open chat, and expires finished agent jobs", async () => {
  mocks.api.list_running_agents.mockResolvedValue(["other"]);
  mocks.api.load_messages.mockResolvedValue([{ id: 1, role: "tool", text: "", tool: start().tool }]);
  const stop = subscribeAgentBackgroundActivity();
  await act(async () => {});
  expect(getBackgroundJobs().find((j) => j.toolId)?.title).toBe("py -3 -m pytest");
  applyAgentBackgroundEvent({ type: "agent_stopped", conv_id: "other", reason: "done" });
  vi.advanceTimersByTime(65_000);
  expect(getBackgroundJobs()).toEqual([]);
  stop();
  expect(mocks.listener).toBeNull();
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


it("pressing a running header row requests its chat and tool", async () => {
  render(<BackgroundActivityDropdown />);
  await act(async () => {});
  act(() => mocks.listener!(start("click-chat", "click-tool")));
  fireEvent.click(screen.getByLabelText("Background activity"));
  fireEvent.click(screen.getByText("py -3 -m pytest"));
  expect(mocks.open).toHaveBeenCalledWith("click-chat");
  function Target() { return <span>{useToolActivityTarget("click-chat")?.toolId}</span>; }
  render(<Target />);
  expect(screen.getByText("click-tool")).toBeTruthy();
});

it("does not evict a running command when many other jobs arrive", () => {
  applyAgentBackgroundEvent(start(), "Coordinator");
  for (let i = 0; i < 80; i++) upsertBackgroundJob({ id: `history:${i}`, phase: "done" });
  expect(getBackgroundJobs().find((j) => j.toolId === "call-1")?.phase).toBe("working");
  expect(countWorkingBackgroundJobs(getBackgroundJobs())).toBe(2);
});
