// @vitest-environment jsdom
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UndoHistoryProvider, useUndoHistoryOptional } from "../navigation/UndoHistoryContext";
import { useUndoShortcuts } from "../navigation/useUndoShortcuts";
import type { AutomationSummaryDto, WorkflowOwnerDto } from "../types/panel";
import { WorkflowList } from "./WorkflowList";

vi.mock("../hooks/usePanelApi", () => ({ getApi: () => ({}) }));

const LOCAL: WorkflowOwnerDto = { id: "local", kind: "local", label: "Local", readOnly: false };
const TEAM: WorkflowOwnerDto = { id: "teamA", kind: "team", label: "Alpha", readOnly: false };
const LOCKED: WorkflowOwnerDto = { id: "teamB", kind: "team", label: "Beta", readOnly: true, reason: "No Manage automations" };

const row = (id: string, folder: string, owner = LOCAL): AutomationSummaryDto =>
  ({ id, name: id, enabled: true, folder, owner, trigger: { kind: "manual", label: "Manual" } }) as AutomationSummaryDto;

function Shortcuts() {
  const history = useUndoHistoryOptional()!;
  useUndoShortcuts(history.undo, history.redo);
  return null;
}

function mount(onMoveWorkflow = vi.fn(async () => undefined), onMoveFolder = vi.fn(async () => undefined)) {
  render(
    <UndoHistoryProvider>
      <Shortcuts />
      <WorkflowList listId="list" listRef={{ current: null }} owners={{ owners: [LOCAL, TEAM, LOCKED] }}
        rows={[row("Main", "Kit"), row("Helper", "Kit/Lib"), row("Loose", ""), row("Shared", "", TEAM)]} activeId=""
        collapsed={false} nowMs={0} onToggleCollapsed={() => {}} onOpen={() => {}} onCreate={() => {}} onImportLocal={() => {}}
        onMoveWorkflow={onMoveWorkflow} onMoveFolder={onMoveFolder} />
    </UndoHistoryProvider>,
  );
  return { onMoveWorkflow, onMoveFolder };
}

const transfer = () => ({ setData: vi.fn(), getData: vi.fn(), setDragImage: vi.fn(), effectAllowed: "", dropEffect: "", types: [] as string[] });
function drag(kind: "dragStart" | "dragOver" | "drop" | "dragEnd", target: Element, dataTransfer: ReturnType<typeof transfer>) {
  const event = createEvent[kind](target, { dataTransfer });
  Object.defineProperty(event, "clientY", { value: 10 });
  Object.defineProperty(event, "clientX", { value: 40 });
  fireEvent(target, event);
}
const workflowRow = (name: string) => screen.getByText(name).closest("button")!;
const folderRow = (name: string) => screen.getByRole("button", { name: `Folder ${name}` }).closest(".aw-tree-folder")!;
const ownerHead = (name: string) => screen.getByRole("button", { name }).parentElement!;

function dragTo(from: Element, to: Element) {
  const dt = transfer();
  drag("dragStart", from, dt);
  drag("dragOver", to, dt);
  drag("drop", to, dt);
  drag("dragEnd", from, dt);
  return dt;
}

afterEach(cleanup);

describe("Workflows list on the shared drag engine", () => {
  it("files a workflow in a folder with one move", async () => {
    const { onMoveWorkflow } = mount();
    const dt = dragTo(workflowRow("Loose"), folderRow("Lib"));
    expect(dt.setData).toHaveBeenCalledWith("application/x-ducky-workflow-list", "workflow");
    expect(onMoveWorkflow).toHaveBeenCalledTimes(1);
    expect(onMoveWorkflow).toHaveBeenCalledWith("Loose", "local", "Kit/Lib");
  });

  it("dropping on a workflow row files it in that row's folder", () => {
    const { onMoveWorkflow } = mount();
    dragTo(workflowRow("Loose"), workflowRow("Helper"));
    expect(onMoveWorkflow).toHaveBeenCalledExactlyOnceWith("Loose", "local", "Kit/Lib");
  });

  it("moves a folder with everything in it, never into itself", () => {
    const { onMoveFolder } = mount();
    dragTo(folderRow("Kit"), folderRow("Lib"));
    expect(onMoveFolder).not.toHaveBeenCalled();
    dragTo(folderRow("Lib"), ownerHead("Local"));
    expect(onMoveFolder).toHaveBeenCalledExactlyOnceWith("local", "Kit/Lib", "Lib");
  });

  it("never files into another owner or a read-only team", () => {
    const { onMoveWorkflow } = mount();
    dragTo(workflowRow("Loose"), ownerHead("Team · Alpha"));
    dragTo(workflowRow("Loose"), ownerHead("Team · Beta"));
    dragTo(workflowRow("Shared"), ownerHead("Local"));
    expect(onMoveWorkflow).not.toHaveBeenCalled();
  });

  it("Ctrl+Z in the Workflows list puts the workflow back; Ctrl+Y files it again", async () => {
    const { onMoveWorkflow } = mount();
    dragTo(workflowRow("Loose"), folderRow("Kit"));
    expect(onMoveWorkflow).toHaveBeenLastCalledWith("Loose", "local", "Kit");
    const list = screen.getByRole("complementary", { name: "Workflows" });
    act(() => list.focus());
    await act(async () => {
      fireEvent.keyDown(list, { key: "z", ctrlKey: true });
    });
    await waitFor(() => expect(onMoveWorkflow).toHaveBeenLastCalledWith("Loose", "local", ""));
    await act(async () => {
      fireEvent.keyDown(list, { key: "y", ctrlKey: true });
    });
    await waitFor(() => expect(onMoveWorkflow).toHaveBeenLastCalledWith("Loose", "local", "Kit"));
    expect(onMoveWorkflow).toHaveBeenCalledTimes(3);
  });
});
