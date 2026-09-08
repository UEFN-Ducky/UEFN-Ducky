import { describe, expect, it } from "vitest";

import fixture from "../../../../../backend/workspace/schemas/fixtures/changeset_run.json";
import type { ChangesetRunDto } from "../types/panel";
import {
  blockedRows,
  changesetFilePaths,
  changesetRunSummary,
  conflictCountsByConv,
  editorVerb,
  groupChangesetEntries,
  groupEditorEntries,
  runTimeline,
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

function editorEntry(over: Record<string, unknown> = {}, editor: Record<string, unknown> = {}) {
  return {
    path: "uefn://actor/AAA/transform",
    op: "editor" as const,
    tool: "set_actor_transform",
    after_blob: "hash",
    editor: {
      command: "set_actor_transform",
      kind: "actor",
      facet: "transform",
      targets: [{ kind: "actor", id: "AAA", guid: "AAA", label: "VerifyCube" }],
      revertable: "auto",
      summary: "moved +250 on Z",
      ...editor,
    },
    ...over,
  };
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

  it("never lets an editor slot or a blocked attempt into the file list", () => {
    const r = withEntries([
      { path: "a" },
      editorEntry(),
      { path: "b", outcome: "blocked", reason: "out of lane" },
      { path: "c", outcome: "failed", reason: "disk full" },
    ]);
    // A uefn:// slot has no basename and nothing to open; a refusal changed nothing.
    expect(groupChangesetEntries(r).map((x) => x.path)).toEqual(["a"]);
    expect(changesetFilePaths([r])).toEqual(["a"]);
  });
});

describe("groupEditorEntries", () => {
  it("collapses repeated edits to one target into one row", () => {
    const r = withEntries([
      editorEntry({ seq: 1 }, { summary: "moved +100 on Z" }),
      editorEntry({ seq: 2 }, { summary: "moved +250 on Z" }),
    ]);
    const [row] = groupEditorEntries(r);
    expect(row.seqs).toEqual([1, 2]);
    expect(row.label).toBe("VerifyCube");
    expect(row.verb).toBe("moved");
    // The newest summary wins; every step stays available behind the expander.
    expect(row.detail).toBe("moved +250 on Z");
    expect(row.steps.map((s) => s.summary)).toEqual(["moved +100 on Z", "moved +250 on Z"]);
  });

  it("a run is only auto-revertable while every step in it is", () => {
    const r = withEntries([
      editorEntry({}, { revertable: "auto" }),
      editorEntry({}, { revertable: "manual", reason: "the rows were replaced" }),
    ]);
    const [row] = groupEditorEntries(r);
    expect(row.revertable).toBe("manual");
    expect(row.reason).toBe("the rows were replaced");
  });

  it("reads a creation as spawned and counts what it made", () => {
    const r = withEntries([
      editorEntry(
        { path: "uefn://actor/BBB/exists", tool: "spawn_actor" },
        {
          command: "spawn_actor",
          facet: "exists",
          created: [{ kind: "actor", id: "BBB", label: "New" }],
          summary: "spawned New",
        },
      ),
    ]);
    const [row] = groupEditorEntries(r);
    expect(row.verb).toBe("spawned");
    expect(row.createdCount).toBe(1);
  });

  it("falls back to the command when no facet verb fits", () => {
    expect(editorVerb("fill_data_table_from_json", "", 0)).toBe("fill data table from json");
    expect(editorVerb("duplicate_asset", "", 2)).toBe("created");
  });
});

describe("blockedRows", () => {
  it("keeps every refusal separate rather than collapsing them", () => {
    const r = withEntries([
      { path: "hub.verse", outcome: "blocked", reason: "out of lane" },
      { path: "hub.verse", outcome: "blocked", reason: "out of lane" },
    ]);
    const rows = blockedRows(r);
    // Two attempts, two rows: "tried twice and was refused twice" is the point.
    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.outcome === "blocked" && row.reason === "out of lane")).toBe(true);
  });

  it("a blocked editor change keeps its target, not a file path", () => {
    const r = withEntries([editorEntry({ outcome: "blocked", reason: "Refused: never delete island content" })]);
    const [row] = blockedRows(r);
    expect(row.kind).toBe("editor");
    if (row.kind === "editor") expect(row.label).toBe("VerifyCube");
    expect(row.reason).toContain("never delete");
  });
});

describe("runTimeline", () => {
  it("reads in the order things happened, files and editor interleaved", () => {
    const r = withEntries([
      { path: "shop.verse" },
      editorEntry({ seq: 2 }),
      { path: "hub.verse", outcome: "blocked", reason: "out of lane" },
    ]);
    expect(runTimeline(r).map((row) => [row.kind, row.outcome])).toEqual([
      ["file", "ok"],
      ["editor", "ok"],
      ["file", "blocked"],
    ]);
  });

  it("the fixture reads as write, create, moved, blocked", () => {
    const rows = runTimeline(run);
    expect(rows.map((row) => row.firstSeq)).toEqual([1, 2, 3, 4]);
    expect(rows[2].kind).toBe("editor");
    expect(rows[3].outcome).toBe("blocked");
  });
});

describe("summaries", () => {
  it("run summary counts files, editor changes and blocked attempts apart", () => {
    const s = changesetRunSummary(run);
    expect(s).toMatchObject({
      files: 2,
      editor: 1,
      blocked: 1,
      manual: 0,
      entries: 3,
      conflicts: 1,
      outOfLane: 0,
      reverted: false,
      running: false,
    });
  });

  it("counts editor changes that have to be undone by hand", () => {
    const r = withEntries([editorEntry({}, { revertable: "manual", inverse: [] })]);
    expect(changesetRunSummary(r).manual).toBe(1);
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
