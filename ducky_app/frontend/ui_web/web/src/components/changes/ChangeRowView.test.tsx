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
    renderRow(fileRow(), { onOpenFile });
    fireEvent.click(screen.getByText("shop.verse"));
    expect(onOpenFile).toHaveBeenCalledWith("Content/Verse/shop.verse", "shop.verse");
  });

  it("an editor row has no file icon and nothing to open", () => {
    const onOpenFile = vi.fn();
    const { container } = renderRow(editorRow(), { onOpenFile });
    // The slot is a uefn:// target, not a path: opening it would be a 404 at best.
    expect(container.querySelector(".changes-row-icon")).toBeNull();
    expect(container.querySelector(".changes-row-name--file")).toBeNull();
    fireEvent.click(screen.getByText("VerifyCube"));
    expect(onOpenFile).not.toHaveBeenCalled();
    expect(screen.getByText("moved")).toBeTruthy();
    expect(screen.getByText("Auto")).toBeTruthy();
  });

  it("a blocked row shows its reason and offers no revert", () => {
    const { container } = renderRow(
      fileRow({ outcome: "blocked", reason: "Content/Verse/Hub/hub.verse is outside Hacker's lane", hasDiff: false }),
    );
    expect(screen.getByText("BLOCKED")).toBeTruthy();
    expect(screen.getByText(/outside Hacker's lane/)).toBeTruthy();
    expect(container.querySelectorAll("button.changeset-btn")).toHaveLength(0);
  });

  it("a reverted row cannot be reverted again", () => {
    const { container } = renderRow(editorRow({ reverted: true }));
    expect(screen.getByText("Reverted")).toBeTruthy();
    expect(container.querySelectorAll("button.changeset-btn")).toHaveLength(0);
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

  it("a run that is itself a revert offers no revert button", () => {
    const { container } = render(
      <ChangeRowView
        run={{ ...run, source: "revert" } as ChangesetRunDto}
        row={fileRow({ hasDiff: false })}
        busy={false}
        expanded={false}
        onToggleExpanded={() => {}}
        onDiff={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(container.querySelectorAll("button.changeset-btn")).toHaveLength(0);
  });
});
