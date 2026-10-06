// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AutomationDto } from "../types/panel";
import { guardUnsavedWorkflow, setUnsavedWorkflow, unsavedWorkflow, UnsavedWorkflowPrompt } from "./unsavedWorkflow";

const api = vi.hoisted(() => ({ save_workflow: vi.fn(), workflow_sync: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));

const edited: AutomationDto = { id: "p", name: "Example", enabled: true, folder: "Tests", graph: { nodes: [{ id: "s", type: "start.chat", x: 40, y: 0, config: {} }], edges: [] } };

afterEach(() => { cleanup(); setUnsavedWorkflow(null); window.localStorage.clear(); vi.clearAllMocks(); });

describe("closing Workflows with unsaved edits (its editor in the background)", () => {
  it("lets go at once when nothing is unsaved", async () => {
    render(<UnsavedWorkflowPrompt />);
    await expect(guardUnsavedWorkflow()).resolves.toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Save sends the kept edits (never refiling the workflow), then lets go", async () => {
    api.save_workflow.mockResolvedValue({ workflow: { ...edited, owner: { id: "local", kind: "local" } } });
    render(<UnsavedWorkflowPrompt />);
    setUnsavedWorkflow(edited);
    let answer: Promise<boolean> = Promise.resolve(false);
    act(() => { answer = guardUnsavedWorkflow(); });
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Example");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await expect(answer).resolves.toBe(true);
    const { folder: _folder, ...body } = edited;
    expect(api.save_workflow).toHaveBeenCalledWith(body);
    expect(unsavedWorkflow()).toBeNull();
  });

  it("a failed save says why and stays open; Don't save drops the edits", async () => {
    api.save_workflow.mockResolvedValue({ error: "The disk is full." });
    render(<UnsavedWorkflowPrompt />);
    setUnsavedWorkflow(edited);
    let answer: Promise<boolean> = Promise.resolve(false);
    act(() => { answer = guardUnsavedWorkflow(); });
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toContain("The disk is full."));
    fireEvent.click(within(dialog).getByRole("button", { name: "Don't save" }));
    await expect(answer).resolves.toBe(true);
    expect(unsavedWorkflow()).toBeNull();
  });

  it("Cancel keeps the tab and the edits", async () => {
    render(<UnsavedWorkflowPrompt />);
    setUnsavedWorkflow(edited);
    let answer: Promise<boolean> = Promise.resolve(true);
    act(() => { answer = guardUnsavedWorkflow(); });
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await expect(answer).resolves.toBe(false);
    expect(unsavedWorkflow()).toBe(edited);
    expect(api.save_workflow).not.toHaveBeenCalled();
  });
});
