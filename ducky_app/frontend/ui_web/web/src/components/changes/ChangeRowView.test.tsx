// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChangesetRunDto } from "../../types/panel";
import type { ChangeRow } from "../../utils/changesetGrouping";
import { ChangeRowView } from "./ChangeRowView";

afterEach(cleanup);

const run = { run_id: "r1", status: "done", source: "agent", ducky_name: "Hacker" } as ChangesetRunDto;

function fileRow(over: Partial<Extract<ChangeRow, { kind: "file" }>> = {}): ChangeRow {
  return {
    kind: "file",
    key: "file:Content/Verse/shop.verse",
    path: "Content/Verse/shop.verse",
    op: "write",
    fromPath: null,
    seqs: [1],
    firstSeq: 1,
    lastSeq: 1,
    firstTs: 1757250000,
    lastTs: 1757250000,
    linesAdded: 12,
    linesRemoved: 3,
    conflict: null,
    outOfLane: false,
    reverted: false,
    revertedByRun: "",
    hasDiff: true,
    outcome: "ok",
    reason: "",
    steps: [],
    ...over,
  } as ChangeRow;
}

function editorRow(over: Partial<Extract<ChangeRow, { kind: "editor" }>> = {}): ChangeRow {
  return {
    kind: "editor",
    key: "editor:uefn://actor/AAA/transform",
    slot: "uefn://actor/AAA/transform",
    command: "set_actor_transform",
    verb: "moved",
    targetKind: "actor",
    facet: "transform",
    label: "VerifyCube",
    detail: "moved +250 on Z",
    revertable: "auto",
    createdCount: 0,
    hasDiff: true,
    seqs: [1],
    firstSeq: 1,
    lastSeq: 1,
    firstTs: 1757250000,
    lastTs: 1757250000,
    outcome: "ok",
    reason: "",
    revertedByRun: "",
    steps: [],
    ...over,
  } as ChangeRow;
}

function renderRow(row: ChangeRow, props: Partial<Parameters<typeof ChangeRowView>[0]> = {}) {
  return render(
    <ChangeRowView
      run={run}
      row={row}
      busy={false}
      expanded={false}
      onToggleExpanded={() => {}}
      onDiff={() => {}}
      onRevert={() => {}}
      {...props}
    />,
  );
}

describe("ChangeRowView", () => {
  it("a file row opens the file", () => {
    const onOpenFile = vi.fn();
    const onDiff = vi.fn();
    renderRow(fileRow(), { onOpenFile, onDiff });
    fireEvent.click(screen.getByText("shop.verse"));
    expect(onOpenFile).toHaveBeenCalledWith("Content/Verse/shop.verse", "shop.verse");
    expect(onDiff).not.toHaveBeenCalled();
  });

  it("an editor row has no file icon and nothing to open", () => {
    const onOpenFile = vi.fn();
    const onDiff = vi.fn();
    const { container } = renderRow(editorRow(), { onOpenFile, onDiff });
    // The slot is a uefn:// target, not a path: opening it would be a 404 at best.
    expect(container.querySelector(".changes-row-icon")).toBeNull();
    expect(container.querySelector(".changes-row-name--file")).toBeNull();
    fireEvent.click(screen.getByText("VerifyCube"));
    expect(onOpenFile).not.toHaveBeenCalled();
    expect(onDiff).toHaveBeenCalledOnce();
    expect(screen.getByText("moved")).toBeTruthy();
    expect(screen.getByText("Auto")).toBeTruthy();
  });

  it("a blocked row shows its reason and offers no revert", () => {
    const { container } = renderRow(
      fileRow({ outcome: "blocked", reason: "Content/Verse/Hub/hub.verse is outside Hacker's lane", hasDiff: false }),
    );
    expect(screen.getByText("BLOCKED")).toBeTruthy();
    // Blocked rows stay one line: the reason is the row's tooltip and the click-popup.
    expect(screen.getByTitle(/outside Hacker's lane/)).toBeTruthy();
    expect(container.querySelectorAll("button.changeset-btn")).toHaveLength(0);
  });

  it("an archived run keeps Diff but locks Revert", () => {
    renderRow(fileRow(), { run: { ...run, archived: true } as ChangesetRunDto });
    expect(screen.getByRole("button", { name: "Diff" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Revert" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("grays out the row and blocks clicks while that revert is running", () => {
    const onRevert = vi.fn();
    const onContext = vi.fn();
    const { container } = renderRow(fileRow(), {
      busy: true,
      reverting: true,
      onRevert,
      onContextMenu: onContext,
    });
    const row = container.querySelector(".changes-row--busy");
    expect(row).toBeTruthy();
    expect(row?.getAttribute("aria-busy")).toBe("true");
    expect((screen.getByRole("button", { name: "Revert" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.contextMenu(row!);
    expect(onContext).not.toHaveBeenCalled();
  });

  it("a live run locks Revert until the agent stops", () => {
    const onRevert = vi.fn();
    renderRow(fileRow(), {
      run: { ...run, status: "running" } as ChangesetRunDto,
      onRevert,
    });
    const btn = screen.getByRole("button", { name: "Revert" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("title")).toMatch(/stop the agent first/i);
    fireEvent.click(btn);
    expect(onRevert).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Diff" })).toBeTruthy();
  });

  it("editor changes with no inverse keep a disabled Revert", () => {
    renderRow(editorRow({ revertable: "none", reason: "no snapshot", hasDiff: false }));
    const btn = screen.getByRole("button", { name: "Revert" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("title")).toMatch(/no snapshot/);
  });

  it("a reverted row cannot be reverted again, but Redo brings it back", () => {
    const onRedo = vi.fn();
    const { container } = renderRow(editorRow({ reverted: true, revertedByRun: "revert:abc" }), {
      canRedo: true,
      onRedo,
    });
    expect(screen.getByText("Reverted")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Revert" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    expect(onRedo).toHaveBeenCalledOnce();
    expect(container.querySelectorAll("button.changeset-btn")).toHaveLength(1);
  });

  it("repeated edits collapse behind an expander that lists every step", () => {
    const steps = [
      { seq: 1, ts: 1757250000, tool: "set_actor_transform", summary: "moved +100 on Z", reverted: false },
      { seq: 2, ts: 1757250010, tool: "set_actor_transform", summary: "moved +250 on Z", reverted: false },
    ];
    const onToggleExpanded = vi.fn();
    const { rerender } = renderRow(editorRow({ steps }), { onToggleExpanded });
    fireEvent.click(screen.getByText("2 steps"));
    expect(onToggleExpanded).toHaveBeenCalled();
    rerender(
      <ChangeRowView
        run={run}
        row={editorRow({ steps })}
        busy
        expanded
        onToggleExpanded={onToggleExpanded}
        onDiff={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByText("moved +100 on Z")).toBeTruthy();
    // Twice: the row detail is the newest step, and the expander lists it again.
    expect(screen.getAllByText("moved +250 on Z")).toHaveLength(2);
  });

  it("clicking a step opens that write's diff", () => {
    const onDiff = vi.fn();
    const steps = [
      { seq: 1, ts: 1757250000, tool: "workspace_write_file", summary: "+414", reverted: false },
      { seq: 2, ts: 1757250010, tool: "workspace_write_file", summary: "+51 −53", reverted: false },
    ];
    renderRow(fileRow({ steps }), { expanded: true, onDiff });
    fireEvent.click(screen.getByText("+414"));
    expect(onDiff).toHaveBeenCalledWith(1);
  });

  it("each expanded step has its own revert", () => {
    const onRevertStep = vi.fn();
    const steps = [
      { seq: 1, ts: 1757250000, tool: "workspace_write_file", summary: "+414", reverted: false },
      { seq: 2, ts: 1757250010, tool: "workspace_write_file", summary: "+51 −53", reverted: false },
    ];
    renderRow(fileRow({ steps }), { expanded: true, onRevertStep });
    fireEvent.click(screen.getByRole("button", { name: "Revert write 1" }));
    expect(onRevertStep).toHaveBeenCalledWith(1);
    expect(onRevertStep).toHaveBeenCalledTimes(1);
  });

  it("a run that is itself a revert offers Redo, not Revert", () => {
    render(
      <ChangeRowView
        run={{ ...run, source: "revert" } as ChangesetRunDto}
        row={fileRow({ hasDiff: false })}
        busy={false}
        expanded={false}
        redo
        onToggleExpanded={() => {}}
        onDiff={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Redo" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Revert" })).toBeNull();
  });
});
