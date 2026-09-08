import { describe, expect, it } from "vitest";

import fixture from "../../../../../backend/workspace/schemas/fixtures/changeset_run.json";
import type { ChangesetRunDto } from "../types/panel";
import {
  changesetFilePaths,
  changesetRunSummary,
  conflictCountsByConv,
  groupChangesetEntries,
  statusLabel,
} from "./changesetGrouping";

const run = fixture as unknown as ChangesetRunDto;

function withEntries(entries: Partial<ChangesetRunDto["entries"][number]>[], extra: Partial<ChangesetRunDto> = {}): ChangesetRunDto {
  return {
    ...run,
    ...extra,
    entries: entries.map((e, i) => ({
      seq: i + 1,
      ts: 1000 + i,
      path: "Content/Verse/a.verse",
      op: "write",
      tool: "workspace_write_file",
      before_hash: "",
      after_hash: "",
      in_lane: null,
      ...e,
    })),
  } as ChangesetRunDto;
}

describe("groupChangesetEntries", () => {
  it("collapses writes per path and keeps latest conflict / any out-of-lane", () => {
    const r = withEntries([
      { path: "a", lines_added: 2 },
      { path: "b", op: "create", in_lane: false },
      { path: "a", lines_added: 3, lines_removed: 1, conflict: { kind: "stale_base" } },
    ]);
    const rows = groupChangesetEntries(r);
    expect(rows.map((x) => x.path)).toEqual(["a", "b"]);
    const a = rows[0];
    expect(a.seqs).toEqual([1, 3]);
    expect(a.linesAdded).toBe(5);
    expect(a.linesRemoved).toBe(1);
    expect(a.conflict?.kind).toBe("stale_base");
    expect(a.outOfLane).toBe(false);
    expect(rows[1].outOfLane).toBe(true);
  });

  it("reads the schema fixture", () => {
    const rows = groupChangesetEntries(run);
    expect(rows).toHaveLength(2);
    expect(rows[0].path).toBe("Content/Verse/Shop/shop_ui.verse");
    expect(rows[0].conflict?.kind).toBe("concurrent_writer");
    expect(rows[1].hasDiff).toBe(true);
  });

  it("marks a path reverted only when every entry was reverted", () => {
    const r = withEntries([{ path: "a", reverted: true }, { path: "a", reverted: false }]);
    expect(groupChangesetEntries(r)[0].reverted).toBe(false);
  });
});

describe("summaries", () => {
  it("run summary counts files, conflicts and out-of-lane rows", () => {
    const s = changesetRunSummary(run);
    expect(s).toMatchObject({ files: 2, entries: 2, conflicts: 1, outOfLane: 0, reverted: false, running: false });
  });

  it("file paths dedupe across runs and skip reverted", () => {
    const older = withEntries([{ path: "x" }], { run_id: "o", started: 1 });
    const newer = withEntries([{ path: "x" }, { path: "y", reverted: true }], { run_id: "n", started: 2 });
    expect(changesetFilePaths([older, newer])).toEqual(["x"]);
  });

  it("conflict counts only for running runs", () => {
    const running = withEntries([{ conflict: { kind: "stale_base" } }], { status: "running", conv_id: "hacker" });
    const done = withEntries([{ conflict: { kind: "stale_base" } }], { status: "done", conv_id: "artist" });
    expect(conflictCountsByConv([running, done])).toEqual({ hacker: 1 });
  });

  it("status labels", () => {
    expect(statusLabel("partially_reverted")).toBe("Partly reverted");
    expect(statusLabel("cancelled")).toBe("Stopped");
  });
});
