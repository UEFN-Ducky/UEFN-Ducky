// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BackgroundActivityDropdown } from "./BackgroundActivityDropdown";
import { _resetBackgroundActivityForTests, upsertBackgroundJob } from "../hooks/backgroundActivity";

const api = vi.hoisted(() => ({ stop_workflow: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
afterEach(() => { cleanup(); _resetBackgroundActivityForTests(); vi.clearAllMocks(); });

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
