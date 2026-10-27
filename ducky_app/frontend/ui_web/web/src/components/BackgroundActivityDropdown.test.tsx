// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackgroundActivityDropdown } from "./BackgroundActivityDropdown";
import { _resetBackgroundActivityForTests, onBackgroundJobAction, upsertBackgroundJob } from "../hooks/backgroundActivity";
import { resetWorkflowRunsForTests } from "../hooks/workflowRunsByChat";
import { runAskUser } from "../ask-user";
import { _resetAskUserForTests } from "../ask-user/runAskUser";

const api = vi.hoisted(() => ({ stop_workflow: vi.fn(), workflow_run_snapshot: vi.fn(), plugin_call: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
afterEach(() => {
  cleanup(); _resetBackgroundActivityForTests(); resetWorkflowRunsForTests(); _resetAskUserForTests(); vi.resetAllMocks();
});

it("opens by itself for a question no chat owns, so a publish confirm can't wait unseen", async () => {
  render(<BackgroundActivityDropdown />);
  expect(screen.queryByRole("dialog", { name: "Background activity" })).toBeNull();
  act(() => {
    void runAskUser([{ id: "ok", prompt: "Publish 'wiki-mde' to the team?",
      options: [{ id: "yes", label: "Yes" }, { id: "no", label: "No" }] }], "Publish plugin");
  });
  await screen.findByRole("dialog", { name: "Background activity" });
  expect(screen.getByText("Publish 'wiki-mde' to the team?")).toBeTruthy();
  // Closed again, it stays closed for the same question.
  fireEvent.click(screen.getByRole("button", { name: "Background activity" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Background activity" })).toBeNull());
});

it("stops a specific concurrent workflow from the header activity tray", async () => {
  api.stop_workflow.mockResolvedValue({ ok: true, stopped: true });
  render(<BackgroundActivityDropdown />);
  act(() => {
    for (const run of ["first", "second"]) upsertBackgroundJob({
      id: "graph-run:wf:" + run, source: "workflow", title: run,
      phase: "working", cancelable: true,
    });
  });
  fireEvent.click(screen.getByLabelText("Background activity"));
  expect(screen.getAllByText("Stop")).toHaveLength(2);
  fireEvent.click(screen.getAllByText("Stop")[0]);
  await waitFor(() => expect(api.stop_workflow).toHaveBeenCalledWith("wf", "second"));
});

it("Cancel on a plugin's job row calls that plugin's cancel_job", async () => {
  api.plugin_call.mockResolvedValue({ ok: true });
  render(<BackgroundActivityDropdown />);
  act(() => upsertBackgroundJob({
    id: "plugin:rea:setup", source: "plugin", title: "Reverse Engineer setup",
    detail: "Downloading Node", percent: 30, phase: "working", cancelable: true,
  }));
  fireEvent.click(screen.getByLabelText("Background activity"));
  fireEvent.click(screen.getByText("Cancel"));
  await waitFor(() => expect(api.plugin_call).toHaveBeenCalledWith("rea", "cancel_job", { job_id: "plugin:rea:setup" }));
});

it("refreshes an already ended run without leaving a false Running row or warning", async () => {
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [
    { type: "workflow_run", id: "wf", run: "first", state: "started", name: "Concurrent", conv: "" },
    { type: "workflow_run", id: "wf", run: "second", state: "started", name: "Other", conv: "" },
  ] });
  render(<BackgroundActivityDropdown />);
  fireEvent.click(screen.getByLabelText("Background activity"));
  await waitFor(() => expect(screen.getAllByText("Stop")).toHaveLength(2));
  api.stop_workflow.mockResolvedValue({ ok: true, stopped: false });
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [
    { type: "workflow_run", id: "wf", run: "first", state: "started", name: "Concurrent", conv: "" },
    { type: "workflow_run", id: "wf", run: "first", state: "stopped" },
    { type: "workflow_run", id: "wf", run: "second", state: "started", name: "Other", conv: "" },
  ] });
  fireEvent.click(screen.getByText("Workflow: Concurrent").closest(".connection-status-menu-row")!.querySelector("button")!);
  await waitFor(() => expect(screen.getAllByText("Stop")).toHaveLength(1));
  expect(screen.getByText("Stopped")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(api.stop_workflow).toHaveBeenCalledWith("wf", "first");
});

it("reconciles a restored stale tray entry without opening a workflow editor or chat", async () => {
  upsertBackgroundJob({ id: "graph-run:wf:gone", source: "workflow", title: "Concurrent", phase: "working", cancelable: true });
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [] });
  render(<BackgroundActivityDropdown />);
  fireEvent.click(screen.getByLabelText("Background activity"));
  await screen.findByText("No longer running");
  expect(screen.queryByText("Stop")).toBeNull();
  expect(screen.getByText("Nothing running. You can keep working.")).toBeTruthy();
});

it("a row's own button (Release) is a real button whose press its owner hears", () => {
  const heard: string[] = [];
  const stop = onBackgroundJobAction((id) => heard.push(id));
  render(<BackgroundActivityDropdown />);
  act(() => upsertBackgroundJob({
    id: "scope-asked:forge:doc:scene.a", source: "plugin-data", title: "Forge",
    detail: "Ben asks you to release Scene A.", phase: "done", action: "Release",
  }));
  fireEvent.click(screen.getByLabelText("Background activity"));
  fireEvent.click(screen.getByRole("button", { name: "Release" }));
  stop();
  expect(heard).toEqual(["scope-asked:forge:doc:scene.a"]);
});

it("reports cancellation errors instead of silently ignoring Stop", async () => {
  api.stop_workflow.mockRejectedValue(new Error("Disconnected"));
  render(<BackgroundActivityDropdown />);
  act(() => upsertBackgroundJob({
    id: "graph-run:wf:first", source: "workflow", title: "Release",
    phase: "working", cancelable: true,
  }));
  fireEvent.click(screen.getByLabelText("Background activity"));
  fireEvent.click(screen.getByText("Stop"));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Disconnected");
});

it("does not re-render every second while closed and idle", async () => {
  vi.useFakeTimers();
  const setIntervalSpy = vi.spyOn(globalThis, "setInterval");
  try {
    let commits = 0;
    render(<Profiler id="tray" onRender={() => { commits += 1; }}><BackgroundActivityDropdown /></Profiler>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const commitsBefore = commits;
    for (let i = 0; i < 10; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(commits).toBe(commitsBefore);
    expect(setIntervalSpy.mock.calls.filter(([, ms]) => ms === 1000)).toHaveLength(0);
  } finally {
    setIntervalSpy.mockRestore();
    vi.useRealTimers();
  }
});

it("ticks the elapsed time of a running job while the tray is open", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
  try {
    render(<BackgroundActivityDropdown />);
    act(() => upsertBackgroundJob({
      id: "job:render", source: "agent", title: "Render", detail: "Blender",
      phase: "working", startedAt: Date.now() - 5000,
    }));
    fireEvent.click(screen.getByLabelText("Background activity"));
    expect(screen.getByText(/Blender/).textContent).toContain("5.0s");
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByText(/Blender/).textContent).toContain("8.0s");
  } finally {
    vi.useRealTimers();
  }
});
