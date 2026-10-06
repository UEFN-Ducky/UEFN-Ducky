// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkflowList } from "./WorkflowList";
import type { AutomationSummaryDto, WorkflowOwnerDto } from "../types/panel";

vi.mock("../hooks/usePanelApi", () => ({ getApi: () => ({}) }));

const LOCAL: WorkflowOwnerDto = { id: "local", kind: "local", label: "Local", readOnly: false };
const ALPHA: WorkflowOwnerDto = { id: "teamA", kind: "team", label: "Alpha", readOnly: false };
const BETA: WorkflowOwnerDto = { id: "teamB", kind: "team", label: "Beta", readOnly: true, reason: "No Manage automations" };

const row = (id: string, folder: string, owner = LOCAL): AutomationSummaryDto =>
  ({ id, name: id, enabled: true, folder, owner, trigger: { kind: "manual", label: "Manual" } }) as AutomationSummaryDto;

function renderList(onCopyFolder = vi.fn(), rows = [row("Main", "Kit"), row("Helper", "Kit/Lib"), row("Loose", "")]) {
  render(
    <WorkflowList listId="list" listRef={{ current: null }} owners={{ owners: [LOCAL, ALPHA, BETA] }} rows={rows} activeId=""
      collapsed={false} nowMs={0} emptyFolders={{ local: ["Kit/Lib/Later", "Kit/Art"] }} onToggleCollapsed={() => {}} onOpen={() => {}}
      onCreate={() => {}} onImportLocal={() => {}} onAddFolder={() => {}} onMoveWorkflow={() => {}} onMoveFolder={() => {}}
      onCopyFolder={onCopyFolder} />,
  );
  return onCopyFolder;
}
const items = () => [...screen.getByRole("menu").querySelectorAll('[role^="menuitem"]')].map((el) => el.textContent?.trim());

afterEach(cleanup);

describe("folder menu: copy or move a whole folder to another owner", () => {
  it("offers every other owner it can write to, and says how much goes along", () => {
    const onCopy = renderList();
    fireEvent.contextMenu(screen.getByRole("button", { name: "Folder Kit" }));
    expect(items()).toEqual(["New workflow here", "New folder inside", "Rename", "Copy folder to Team · Alpha", "Move folder to Team · Alpha",
      "Remove folder (keeps its workflows)"]);  // Beta is read-only here
    fireEvent.click(screen.getByRole("menuitem", { name: "Move folder to Team · Alpha" }));
    // Main and Helper; Lib, Lib/Later (empty) and Art (empty).
    expect(onCopy).toHaveBeenCalledWith("local", "Kit", "teamA", true, { workflows: 2, folders: 3 });
  });

  it("copies out of a read-only team but never moves", () => {
    const onCopy = renderList(vi.fn(), [row("Daily", "Ops", BETA)]);
    fireEvent.contextMenu(screen.getByRole("button", { name: "Folder Ops" }));
    expect(items()).toEqual(["Read-only", "Copy folder to Local", "Copy folder to Team · Alpha"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy folder to Local" }));
    expect(onCopy).toHaveBeenCalledWith("teamB", "Ops", "local", false, { workflows: 1, folders: 0 });
  });

  it("has no copy items without the handler", () => {
    render(
      <WorkflowList listId="list" listRef={{ current: null }} owners={{ owners: [LOCAL, ALPHA] }} rows={[row("Main", "Kit")]} activeId=""
        collapsed={false} nowMs={0} onToggleCollapsed={() => {}} onOpen={() => {}} onCreate={() => {}} onImportLocal={() => {}} />,
    );
    fireEvent.contextMenu(screen.getByRole("button", { name: "Folder Kit" }));
    expect(items()).toEqual(["New workflow here"]);
  });
});
