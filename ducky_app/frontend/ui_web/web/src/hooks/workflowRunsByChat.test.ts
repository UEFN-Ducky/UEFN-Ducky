// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { getBackgroundJobs, _resetBackgroundActivityForTests, upsertBackgroundJob } from "./backgroundActivity";
import { act, cleanup, renderHook } from "@testing-library/react";
import { applyWorkflowEvent, clearWorkflowRunHistory, hydrateWorkflowRunHistory, refreshWorkflowRuns, resetWorkflowRunsForTests, snapshotDue, subscribeWorkflowEvents, useWorkflowRuns, useChatWorkflowRuns, dismissWorkflowRun } from "./workflowRunsByChat";
import type { PanelPushEvent } from "../types/panel";

const api = vi.hoisted(() => ({ workflow_run_snapshot: vi.fn(), dismiss_workflow_run: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
afterEach(() => { cleanup(); resetWorkflowRunsForTests(); _resetBackgroundActivityForTests(); vi.resetAllMocks(); });

it("restores saved runs in chronological order without replacing a concurrent live run", () => {
  applyWorkflowEvent(start("live"));
  const { result } = renderHook(() => useWorkflowRuns("wf"));
  act(() => hydrateWorkflowRunHistory("wf", "Example", [
    { run: "old", started: 1, ended: 2, ok: true, steps: [{ id: "a", label: "First", ok: true }], node_outputs: { a: { value: "first output" } } },
    { run: "failed", started: 3, ended: 4, ok: false, error: "No key", steps: [] },
    { run: "live", started: 5, ok: true, steps: [] },
  ]));
  expect(result.current.map(run => run.run)).toEqual(["old", "failed", "live"]);
  expect(result.current[0].result?.node_outputs?.a.value).toBe("first output");
  expect(result.current[1]).toMatchObject({ state: "error", error: "No key" });
  expect(result.current[2].state).toBe("running");
  expect(result.current[2].result).toBeUndefined();
  act(() => hydrateWorkflowRunHistory("wf", "Example", [{ run: "old", started: 1, ended: 2, ok: true, steps: [] }]));
  expect(result.current).toHaveLength(3);
});

it("clears only finished runs for this workflow and prevents replay from restoring them", async () => {
  applyWorkflowEvent(start("done"));
  applyWorkflowEvent({ type: "workflow_run", id: "wf", run: "done", state: "done" });
  applyWorkflowEvent(start("live"));
  applyWorkflowEvent({ type: "workflow_run", id: "other", run: "other-done", state: "started" });
  applyWorkflowEvent({ type: "workflow_run", id: "other", run: "other-done", state: "done" });
  const { result } = renderHook(() => ({ current: useWorkflowRuns("wf"), other: useWorkflowRuns("other") }));
  act(() => expect(clearWorkflowRunHistory("wf")).toEqual(["done"]));
  expect(result.current.current.map(run => run.run)).toEqual(["live"]);
  expect(result.current.other).toHaveLength(1);
  act(() => hydrateWorkflowRunHistory("wf", "Example", [{ run: "done", ok: true, steps: [] }]));
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [
    start("done"), { type: "workflow_run", id: "wf", run: "done", state: "done" }, start("live"),
  ] });
  await act(async () => { await refreshWorkflowRuns(); });
  expect(result.current.current.map(run => run.run)).toEqual(["live"]);
  const replayed: PanelPushEvent[] = [];
  const unsubscribe = subscribeWorkflowEvents(event => replayed.push(event));
  unsubscribe();
  expect(replayed.some(event => event.run === "done")).toBe(false);
});

it("preserves a run that finishes while a clear request is in flight", () => {
  applyWorkflowEvent(start("older"));
  applyWorkflowEvent({ type: "workflow_run", id: "wf", run: "older", state: "done" });
  applyWorkflowEvent(start("newer"));
  const { result } = renderHook(() => useWorkflowRuns("wf"));
  const finishedAtRequest = new Set(result.current.filter(run => run.state !== "running").map(run => run.run));
  act(() => {
    applyWorkflowEvent({ type: "workflow_run", id: "wf", run: "newer", state: "done" });
    clearWorkflowRunHistory("wf", finishedAtRequest);
  });
  expect(result.current.map(run => run.run)).toEqual(["newer"]);
});

it("keeps a closed card hidden after restart and replay without deleting history", async () => {
  const events: PanelPushEvent[] = [
    start("closed"), { type: "workflow_run", id: "wf", run: "closed", state: "done" },
    start("live"),
    { ...start("other"), conv: "other-chat" },
    { type: "workflow_run", id: "wf", run: "other", state: "done" },
  ];
  events.forEach(applyWorkflowEvent);
  api.dismiss_workflow_run.mockResolvedValue({ ok: true });
  const { result } = renderHook(() => ({
    cards: useChatWorkflowRuns("chat"), history: useWorkflowRuns("wf"), other: useChatWorkflowRuns("other-chat"),
  }));
  await act(async () => { await dismissWorkflowRun("chat", "closed"); });
  expect(result.current.cards.map(run => run.run)).toEqual(["live"]);
  expect(result.current.history).toHaveLength(3);
  act(() => resetWorkflowRunsForTests()); // A fresh UI has no in-memory dismissals.
  api.workflow_run_snapshot.mockResolvedValue({
    ok: true, events, dismissed: [{ chat_id: "chat", run_id: "closed" }],
  });
  await act(async () => { await refreshWorkflowRuns(); });
  expect(result.current.cards.map(run => run.run)).toEqual(["live"]);
  expect(result.current.history).toHaveLength(3);
  expect(result.current.other.map(run => run.run)).toEqual(["other"]);
  await act(async () => { await refreshWorkflowRuns(); });
  expect(result.current.cards.map(run => run.run)).toEqual(["live"]);
});

it("never hides an active run or a card belonging to another chat", async () => {
  applyWorkflowEvent(start("live"));
  const { result } = renderHook(() => useChatWorkflowRuns("chat"));
  await dismissWorkflowRun("chat", "live");
  await dismissWorkflowRun("other-chat", "live");
  expect(api.dismiss_workflow_run).not.toHaveBeenCalled();
  expect(result.current).toHaveLength(1);
});

function start(run: string): PanelPushEvent {
  return { type: "workflow_run", id: "wf", run, name: run, state: "started", conv: "chat" };
}

it("keeps a run ended when its finish arrives before its start", async () => {
  applyWorkflowEvent({ type: "workflow_run", id: "wf", run: "done", state: "done" });
  applyWorkflowEvent(start("done"));
  expect(getBackgroundJobs()[0]).toMatchObject({ phase: "done", cancelable: false });
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [start("done"), { type: "workflow_run", id: "wf", run: "done", state: "done" }] });
  await refreshWorkflowRuns();
  expect(getBackgroundJobs()[0].phase).toBe("done");
});

it("recovers a missed finish while leaving another concurrent run active", async () => {
  applyWorkflowEvent(start("done"));
  applyWorkflowEvent(start("live"));
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [
    start("done"), { type: "workflow_run", id: "wf", run: "done", state: "error", error: "Failed" }, start("live"),
  ] });
  expect(await refreshWorkflowRuns()).toBe(true);
  expect(getBackgroundJobs().find(j => j.id.endsWith(":done"))).toMatchObject({ phase: "error", detail: "Failed", cancelable: false });
  expect(getBackgroundJobs().find(j => j.id.endsWith(":live"))).toMatchObject({ phase: "working", cancelable: true });
});

it("reconciles both a forgotten run and a legacy persisted job", async () => {
  applyWorkflowEvent(start("gone"));
  upsertBackgroundJob({ id: "graph:legacy", title: "Old", phase: "working", cancelable: true });
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [] });
  await refreshWorkflowRuns();
  expect(getBackgroundJobs()).toHaveLength(2);
  expect(getBackgroundJobs().every(j => j.phase === "done" && !j.cancelable)).toBe(true);
});

it("keeps Running when the bridge fails or refuses the snapshot", async () => {
  applyWorkflowEvent(start("live"));
  api.workflow_run_snapshot.mockRejectedValueOnce(new Error("Disconnected"));
  expect(await refreshWorkflowRuns()).toBe(false);
  api.workflow_run_snapshot.mockResolvedValueOnce({ ok: false, events: [] });
  expect(await refreshWorkflowRuns()).toBe(false);
  expect(getBackgroundJobs()[0]).toMatchObject({ phase: "working", cancelable: true });
});

it("preserves a new run and a newer push while a snapshot is in flight", async () => {
  applyWorkflowEvent(start("existing"));
  let resolve!: (value: { ok: boolean; events: PanelPushEvent[] }) => void;
  api.workflow_run_snapshot.mockReturnValue(new Promise(r => { resolve = r; }));
  const refreshing = refreshWorkflowRuns();
  applyWorkflowEvent(start("new"));
  applyWorkflowEvent({ type: "workflow_step", id: "wf", run: "existing", node: "next", state: "running" });
  upsertBackgroundJob({ id: "graph-run:wf:existing", detail: "Next step" });
  resolve({ ok: true, events: [] });
  await refreshing;
  expect(getBackgroundJobs().every(j => j.phase === "working")).toBe(true);
});

it("does not restore dismissed finished activity on repeated polling", async () => {
  const events: PanelPushEvent[] = [start("done"), { type: "workflow_run", id: "wf", run: "done", state: "done" }];
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events });
  await refreshWorkflowRuns();
  _resetBackgroundActivityForTests();
  await refreshWorkflowRuns();
  expect(getBackgroundJobs()).toEqual([]);
});

it("asks for the run snapshot each second only while a workflow runs", () => {
  // An idle panel fetched it every second forever; runs report themselves by push.
  expect(snapshotDue(10_000, true, 9_500)).toBe(true);
  expect(snapshotDue(10_000, false, 9_500)).toBe(false);
  expect(snapshotDue(24_000, false, 9_500)).toBe(false);
  expect(snapshotDue(24_500, false, 9_500)).toBe(true);
});
