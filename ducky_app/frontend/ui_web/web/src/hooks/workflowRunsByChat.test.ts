// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { getBackgroundJobs, _resetBackgroundActivityForTests, upsertBackgroundJob } from "./backgroundActivity";
import { applyWorkflowEvent, refreshWorkflowRuns, resetWorkflowRunsForTests } from "./workflowRunsByChat";
import type { PanelPushEvent } from "../types/panel";

const api = vi.hoisted(() => ({ workflow_run_snapshot: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
afterEach(() => { resetWorkflowRunsForTests(); _resetBackgroundActivityForTests(); vi.resetAllMocks(); });

function start(run: string): PanelPushEvent {
  return { type: "workflow_run", id: "wf", run, name: run, state: "started", conv: "chat" };
}

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
