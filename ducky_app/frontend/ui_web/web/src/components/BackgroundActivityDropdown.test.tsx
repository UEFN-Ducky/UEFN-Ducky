// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BackgroundActivityDropdown } from "./BackgroundActivityDropdown";
import { _resetBackgroundActivityForTests, upsertBackgroundJob } from "../hooks/backgroundActivity";
import { resetWorkflowRunsForTests } from "../hooks/workflowRunsByChat";

const api = vi.hoisted(() => ({ stop_workflow: vi.fn(), workflow_run_snapshot: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
afterEach(() => { cleanup(); _resetBackgroundActivityForTests(); resetWorkflowRunsForTests(); vi.resetAllMocks(); });

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

it("refreshes an already ended run without leaving a false Running row or warning", async () => {
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [
    { type: "workflow_run", id: "wf", run: "first", state: "started", name: "Concurrent", conv: "chat" },
    { type: "workflow_run", id: "wf", run: "second", state: "started", name: "Other", conv: "chat" },
  ] });
  render(<BackgroundActivityDropdown />);
  fireEvent.click(screen.getByLabelText("Background activity"));
  await waitFor(() => expect(screen.getAllByText("Stop")).toHaveLength(2));
  api.stop_workflow.mockResolvedValue({ ok: true, stopped: false });
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [
    { type: "workflow_run", id: "wf", run: "first", state: "started", name: "Concurrent", conv: "chat" },
    { type: "workflow_run", id: "wf", run: "first", state: "stopped" },
    { type: "workflow_run", id: "wf", run: "second", state: "started", name: "Other", conv: "chat" },
  ] });
  fireEvent.click(screen.getByText("Concurrent").closest(".connection-status-menu-row")!.querySelector("button")!);
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
