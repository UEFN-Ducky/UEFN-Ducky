import type { ChangesetConflictDto, ChangesetEntryDto, ChangesetRevertResult, ChangesetRunDto } from "../types/panel";

export type ChangeOutcome = "ok" | "blocked" | "failed";

/** Entries that changed nothing. Recorded so the history is honest, never reverted. */
export function entryOutcome(entry: ChangesetEntryDto): ChangeOutcome {
  const outcome = entry.outcome;
  return outcome === "blocked" || outcome === "failed" ? outcome : "ok";
}

export function isEditorEntry(entry: ChangesetEntryDto): boolean {
  return entry.op === "editor";
}

export function isAppliedEntry(entry: ChangesetEntryDto): boolean {
  return entryOutcome(entry) === "ok";
}

/** A refused or errored attempt: shown in the timeline, absent from every count of work done. */
export function isBlockedEntry(entry: ChangesetEntryDto): boolean {
  return entryOutcome(entry) !== "ok";
}

export interface ChangesetFileRow {
  path: string;
  /** The last operation applied to this path in the run. */
  op: ChangesetEntryDto["op"];
  fromPath: string | null;
  seqs: number[];
  firstSeq: number;
  lastSeq: number;
  linesAdded: number;
  linesRemoved: number;
  /** Latest conflict recorded for this path, if any. */
  conflict: ChangesetConflictDto | null;
  outOfLane: boolean;
  reverted: boolean;
  /** Compensating run that undid this path, when reverted. */
  revertedByRun: string;
  lastTs: number;
  /** Whether a text diff can be shown (write/create entries carry content). */
  hasDiff: boolean;
}

/**
 * One row per path: writes to the same file collapse to the latest state.
 *
 * Files only, and only what actually landed. Editor changes live in pseudo-paths
 * (`uefn://…`) that have no basename, no icon and nothing to open, and blocked
 * attempts changed nothing — letting either into this list would put both into
 * every file count and file-open handler downstream.
 */
export function groupChangesetEntries(run: ChangesetRunDto): ChangesetFileRow[] {
  const byPath = new Map<string, ChangesetFileRow>();
  const entries = [...(run.entries || [])]
    .filter((e) => !isEditorEntry(e) && isAppliedEntry(e))
    .sort((a, b) => a.seq - b.seq);
  for (const e of entries) {
    const row = byPath.get(e.path);
    const isText = e.op === "write" || e.op === "create";
    if (!row) {
      byPath.set(e.path, {
        path: e.path,
        op: e.op,
        fromPath: e.from_path ?? null,
        seqs: [e.seq],
        firstSeq: e.seq,
        lastSeq: e.seq,
        linesAdded: e.lines_added ?? 0,
        linesRemoved: e.lines_removed ?? 0,
        conflict: e.conflict ?? null,
        outOfLane: e.in_lane === false,
        reverted: Boolean(e.reverted),
        revertedByRun: e.reverted_by_run || "",
        lastTs: e.ts,
        hasDiff: isText,
      });
      continue;
    }
    row.op = e.op;
    row.fromPath = e.from_path ?? row.fromPath;
    row.seqs.push(e.seq);
    row.lastSeq = e.seq;
    row.linesAdded += e.lines_added ?? 0;
    row.linesRemoved += e.lines_removed ?? 0;
    if (e.conflict) row.conflict = e.conflict;
    row.outOfLane = row.outOfLane || e.in_lane === false;
    row.reverted = row.reverted && Boolean(e.reverted);
    if (e.reverted_by_run) row.revertedByRun = e.reverted_by_run;
    row.lastTs = Math.max(row.lastTs, e.ts);
    row.hasDiff = row.hasDiff || isText;
  }
  return [...byPath.values()].sort((a, b) => b.lastTs - a.lastTs);
}

// --- editor rows ------------------------------------------------------------------

/** Verb for a facet slot, so a row reads "moved VerifyCube" rather than the command name. */
const VERB_BY_FACET: Record<string, string> = {
  transform: "moved",
  label: "renamed",
  folder: "filed",
  tags: "tagged",
  attach: "attached",
  exists: "created",
  props: "set",
  settings: "configured",
  editable: "wired",
  param: "set",
  rows: "filled",
};

export function editorVerb(command: string, facet: string, created: number): string {
  if (created > 0) return command.startsWith("spawn") ? "spawned" : "created";
  const byFacet = VERB_BY_FACET[facet];
  if (byFacet) return byFacet;
  return (command || "changed").replace(/_/g, " ");
}

export interface ChangeStep {
  seq: number;
  ts: number;
  /** The listener command or host tool that made this step. */
  tool: string;
  summary: string;
  reverted: boolean;
}

interface ChangeRowBase {
  /** Stable React key within a run. */
  key: string;
  seqs: number[];
  firstSeq: number;
  lastSeq: number;
  firstTs: number;
  lastTs: number;
  outcome: ChangeOutcome;
  reason: string;
  reverted: boolean;
  /** The compensating run that undid this row, when it is reverted. */
  revertedByRun: string;
  /** Every entry behind this row, oldest first — what the expander shows. */
  steps: ChangeStep[];
}

export interface FileChangeRow extends ChangesetFileRow, ChangeRowBase {
  kind: "file";
}

export interface EditorChangeRow extends ChangeRowBase {
  kind: "editor";
  /** The `uefn://…` target slot. Never a file path: nothing may try to open it. */
  slot: string;
  command: string;
  verb: string;
  /** actor | asset | device | verse | material | … */
  targetKind: string;
  facet: string;
  /** The program that owns this slot (`uefn`, `blender`, …). */
  program: string;
  /** The target's label, or its path when it has none. */
  label: string;
  /** The listener's own words for what changed, e.g. "moved +250 on Z". */
  detail: string;
  revertable: "auto" | "manual" | "none";
  createdCount: number;
  hasDiff: boolean;
}

export type ChangeRow = FileChangeRow | EditorChangeRow;

function slotLabel(slot: string, entry: ChangesetEntryDto): string {
  const target = entry.editor?.targets?.[0];
  const named = (target?.label || "").trim() || (target?.path || "").trim() || (target?.id || "").trim();
  if (named) return named;
  const rest = slot.replace(/^[a-z][a-z0-9_-]*:\/\//i, "");
  const parts = rest.split("/");
  return parts[1] || parts[0] || slot;
}

/** Files have no scheme. Editor slots are `{program}://kind/id/facet`. */
export function programOfSlot(slot: string): string {
  const i = slot.indexOf("://");
  return i > 0 ? slot.slice(0, i) : "file";
}

export function programLabel(program: string): string {
  if (program === "file") return "Files";
  if (program === "uefn") return "UEFN";
  if (program === "blender") return "Blender";
  if (!program) return "Other";
  return program.charAt(0).toUpperCase() + program.slice(1);
}

export function programOfRow(row: ChangeRow): string {
  const raw = row.kind === "file" ? "file" : row.program || programOfSlot(row.slot);
  return (raw || "other").trim().toLowerCase();
}

/** True when UEFN recorded a blank / all-zero GUID instead of a real actor id. */
export function identLooksEmpty(value: string): boolean {
  const compact = value.replace(/[{}-]/g, "").trim();
  return !compact || /^0+$/.test(compact);
}

/**
 * Group key for one editor change. Repeated edits to the same actor share a
 * slot; a zero GUID must not — those rows are keyed by the Unreal path that
 * is already on the target.
 */
export function editorGroupKey(entry: ChangesetEntryDto): string {
  const slot = entry.path || "";
  const target = entry.editor?.targets?.[0];
  const actorPath = String(target?.path || "").trim();
  const prog = programOfSlot(slot);
  if (!actorPath || prog !== "uefn") return slot;
  const guid = String(target?.guid || target?.id || "");
  const slotId = slot.match(/^[a-z][a-z0-9_-]*:\/\/[^/]+\/([^/]+)\//i)?.[1] || "";
  if (!identLooksEmpty(guid) && !identLooksEmpty(slotId)) return slot;
  const kind = entry.editor?.kind || "actor";
  const facet = entry.editor?.facet || "";
  return `${prog}://${kind}/${actorPath}/${facet}`;
}

/** One row per editor target+facet: three nudges to one actor read as one change. */
export function groupEditorEntries(run: ChangesetRunDto): EditorChangeRow[] {
  const bySlot = new Map<string, EditorChangeRow>();
  const entries = [...(run.entries || [])]
    .filter((e) => isEditorEntry(e) && isAppliedEntry(e))
    .sort((a, b) => a.seq - b.seq);
  for (const e of entries) {
    const editor = e.editor ?? { command: e.tool, revertable: "manual" as const };
    const step: ChangeStep = {
      seq: e.seq,
      ts: e.ts,
      tool: editor.command || e.tool,
      summary: (editor.summary || "").trim(),
      reverted: Boolean(e.reverted),
    };
    const created = (editor.created || []).length;
    const key = editorGroupKey(e);
    const row = bySlot.get(key);
    if (!row) {
      bySlot.set(key, {
        kind: "editor",
        key: `editor:${key}`,
        slot: key,
        command: editor.command || e.tool,
        verb: editorVerb(editor.command || e.tool, editor.facet || "", created),
        targetKind: editor.kind || "other",
        facet: editor.facet || "",
        program: editor.program || programOfSlot(key),
        label: slotLabel(key, e),
        detail: step.summary,
        revertable: editor.revertable || "manual",
        createdCount: created,
        hasDiff: Boolean(e.after_blob) || Boolean(e.before_blob),
        seqs: [e.seq],
        firstSeq: e.seq,
        lastSeq: e.seq,
        firstTs: e.ts,
        lastTs: e.ts,
        outcome: "ok",
        reason: (editor.reason || e.reason || "").trim(),
        reverted: Boolean(e.reverted),
        revertedByRun: e.reverted_by_run || "",
        steps: [step],
      });
      continue;
    }
    row.seqs.push(e.seq);
    row.lastSeq = e.seq;
    row.lastTs = Math.max(row.lastTs, e.ts);
    row.command = editor.command || row.command;
    row.createdCount += created;
    if (created > 0) row.verb = editorVerb(row.command, row.facet, row.createdCount);
    if (step.summary) row.detail = step.summary;
    // A run is only auto-revertable while every step in it is.
    if (editor.revertable === "manual" || editor.revertable === "none") row.revertable = editor.revertable;
    if (!row.reason && editor.reason) row.reason = editor.reason;
    row.hasDiff = row.hasDiff || Boolean(e.after_blob) || Boolean(e.before_blob);
    row.reverted = row.reverted && Boolean(e.reverted);
    if (e.reverted_by_run) row.revertedByRun = e.reverted_by_run;
    row.steps.push(step);
  }
  return [...bySlot.values()];
}

/**
 * Refused and failed attempts, one row each.
 *
 * They never collapse: "tried four times and was refused four times" is the
 * point of recording them, and a collapsed row would hide it.
 */
export function blockedRows(run: ChangesetRunDto): ChangeRow[] {
  return (run.entries || [])
    .filter(isBlockedEntry)
    .map((e) => {
      const outcome = entryOutcome(e);
      const step: ChangeStep = {
        seq: e.seq,
        ts: e.ts,
        tool: e.editor?.command || e.tool,
        summary: (e.editor?.summary || "").trim(),
        reverted: false,
      };
      const base = {
        key: `blocked:${e.seq}`,
        seqs: [e.seq],
        firstSeq: e.seq,
        lastSeq: e.seq,
        firstTs: e.ts,
        lastTs: e.ts,
        outcome,
        reason: (e.reason || "").trim(),
        reverted: false,
        revertedByRun: "",
        steps: [step],
      };
      if (isEditorEntry(e)) {
        const editor = e.editor;
        const row: EditorChangeRow = {
          ...base,
          kind: "editor",
          slot: e.path,
          command: editor?.command || e.tool,
          verb: editorVerb(editor?.command || e.tool, editor?.facet || "", 0),
          targetKind: editor?.kind || "other",
          facet: editor?.facet || "",
          program: editor?.program || programOfSlot(e.path),
          label: slotLabel(e.path, e),
          detail: (editor?.summary || "").trim(),
          revertable: "none",
          createdCount: 0,
          hasDiff: false,
        };
        return row;
      }
      const row: FileChangeRow = {
        ...base,
        kind: "file",
        path: e.path,
        op: e.op,
        fromPath: e.from_path ?? null,
        linesAdded: 0,
        linesRemoved: 0,
        conflict: e.conflict ?? null,
        outOfLane: e.in_lane === false,
        lastTs: e.ts,
        hasDiff: false,
      };
      return row;
    });
}

/**
 * Everything one run did, in the order it happened — files and editor changes
 * interleaved, refusals in place. This is the "change after change" reading.
 */
export function runTimeline(run: ChangesetRunDto): ChangeRow[] {
  const bySeq = new Map((run.entries || []).map((e) => [e.seq, e]));
  const files: FileChangeRow[] = groupChangesetEntries(run).map((row) => {
    const steps: ChangeStep[] = row.seqs.map((seq) => {
      const entry = bySeq.get(seq);
      const added = entry?.lines_added ?? 0;
      const removed = entry?.lines_removed ?? 0;
      const counts = [added ? `+${added}` : "", removed ? `−${removed}` : ""].filter(Boolean).join(" ");
      return {
        seq,
        ts: entry?.ts ?? row.lastTs,
        tool: entry?.tool ?? "",
        summary: counts,
        reverted: Boolean(entry?.reverted),
      };
    });
    return {
      ...row,
      kind: "file" as const,
      key: `file:${row.path}`,
      firstTs: steps[0]?.ts ?? row.lastTs,
      outcome: "ok" as const,
      reason: "",
      revertedByRun: row.revertedByRun || "",
      steps,
    };
  });
  const rows: ChangeRow[] = [...files, ...groupEditorEntries(run), ...blockedRows(run)];
  return rows.sort((a, b) => a.firstSeq - b.firstSeq);
}

export interface ChangesetRunSummary {
  files: number;
  /** Editor changes that landed (actors, devices, assets) — never files. */
  editor: number;
  /** Refused or failed attempts. Nothing changed; nothing to revert. */
  blocked: number;
  /** Editor changes that have to be undone by hand. */
  manual: number;
  entries: number;
  conflicts: number;
  outOfLane: number;
  reverted: boolean;
  partiallyReverted: boolean;
  running: boolean;
}

export function changesetRunSummary(run: ChangesetRunDto): ChangesetRunSummary {
  const rows = groupChangesetEntries(run);
  const editorRows = groupEditorEntries(run);
  const entries = (run.entries || []).filter(isAppliedEntry);
  return {
    files: rows.length,
    editor: editorRows.length,
    blocked: (run.entries || []).filter(isBlockedEntry).length,
    manual: editorRows.filter((r) => r.revertable !== "auto").length,
    entries: entries.length,
    conflicts: rows.filter((r) => r.conflict).length,
    outOfLane: rows.filter((r) => r.outOfLane).length,
    reverted: entries.length > 0 && entries.every((e) => e.reverted),
    partiallyReverted: entries.some((e) => e.reverted) && entries.some((e) => !e.reverted),
    running: run.status === "running",
  };
}

/** True while this run's agent is still writing — revert is locked until Stop. */
export function runAgentLive(run: ChangesetRunDto, runningConvIds?: ReadonlySet<string>): boolean {
  if (run.status === "running") return true;
  const conv = (run.conv_id || "").trim();
  return Boolean(conv && runningConvIds?.has(conv));
}

export function sortRunsNewestFirst(runs: ChangesetRunDto[]): ChangesetRunDto[] {
  return [...runs].sort((a, b) => (b.started || 0) - (a.started || 0));
}

/** Files touched across runs (deduped, newest first) — the "N Files" count. */
export function changesetFilePaths(runs: ChangesetRunDto[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const run of sortRunsNewestFirst(runs)) {
    for (const row of groupChangesetEntries(run)) {
      if (row.reverted || seen.has(row.path)) continue;
      seen.add(row.path);
      out.push(row.path);
    }
  }
  return out;
}

/** Live conflicts per conversation (running runs only) — the red dot on a member chip. */
export function conflictCountsByConv(runs: ChangesetRunDto[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const run of runs) {
    if (run.status !== "running") continue;
    const n = (run.entries || []).filter((e) => e.conflict && !e.reverted).length;
    if (n > 0) counts[run.conv_id] = (counts[run.conv_id] ?? 0) + n;
  }
  return counts;
}

export function statusLabel(status: ChangesetRunDto["status"]): string {
  switch (status) {
    case "running":
      return "Running";
    case "done":
      return "Done";
    case "error":
      return "Errored";
    case "cancelled":
      return "Stopped";
    case "reverted":
      return "Reverted";
    case "partially_reverted":
      return "Partly reverted";
    default:
      return status;
  }
}

export function rowSlot(row: ChangeRow): string {
  return row.kind === "file" ? row.path : row.slot;
}

/** A revert run we can still undo (bring the original work back). */
export function liveRevertRun(
  runs: ChangesetRunDto[],
  runId: string | null | undefined,
): ChangesetRunDto | undefined {
  const id = (runId || "").trim();
  if (!id) return undefined;
  const found = runs.find((r) => r.run_id === id);
  if (found && (found.status === "reverted" || found.archived)) return undefined;
  return found;
}

/** Host for revert_changeset. Old empty-conv_id reverts are missing from the chat list. */
export function revertHost(runs: ChangesetRunDto[], runId: string | null | undefined): ChangesetRunDto | null {
  const id = (runId || "").trim();
  if (!id) return null;
  const live = liveRevertRun(runs, id);
  if (live) return live;
  if (runs.some((r) => r.run_id === id)) return null;
  return {
    schema_version: 1,
    run_id: id,
    conv_id: "",
    started: 0,
    status: "done",
    source: "revert",
    entries: [],
  };
}

/** Badge text: entry flags beat a stale run.status (agent wrote again after revert). */
export function runDisplayStatus(run: ChangesetRunDto, summary: ChangesetRunSummary): string {
  if (run.archived) return "Archived";
  if (summary.reverted) return "Reverted";
  if (summary.partiallyReverted) return "Partly reverted";
  if (run.status === "reverted" || run.status === "partially_reverted") {
    return summary.running ? "Running" : "Done";
  }
  return statusLabel(run.status);
}

function revertFileName(path: string): string {
  return path.replace(/\\/g, "/").split("/").pop() || path;
}

/** Empty means the restore landed. Anything else is the reason it did not. */
export function revertFailureMessage(result: ChangesetRevertResult): string {
  if ((result.blocked_by ?? []).length) return "";
  const parts: string[] = [...result.errors];
  if (result.skipped_modified.length) {
    const names = result.skipped_modified.map((s) => revertFileName(s.path)).join(", ");
    const verb = result.skipped_modified.length === 1 ? "no longer matches" : "no longer match";
    parts.push(
      result.reverted.length
        ? `Restored some files. Not reverted: ${names} ${verb} what this run wrote.`
        : `Not reverted: ${names} ${verb} what this run wrote.`,
    );
  } else if (!result.reverted.length && !(result.manual ?? []).length && !result.errors.length) {
    parts.push("Nothing was reverted.");
  }
  return parts.join(" ");
}

/** Run to revert when Redo is clicked on a run header. Empty = no redo. */
export function runRedoTargetId(run: ChangesetRunDto, runs: ChangesetRunDto[]): string {
  if (run.source === "revert") {
    return run.archived || run.status === "reverted" ? "" : run.run_id;
  }
  const ids = [
    ...new Set(
      (run.entries || []).map((e) => (e.reverted_by_run || "").trim()).filter(Boolean),
    ),
  ];
  for (const id of ids) {
    const found = runs.find((r) => r.run_id === id);
    if (found && (found.status === "reverted" || found.archived)) continue;
    if (found) return id;
  }
  if (
    ids.length &&
    (run.status === "reverted" || run.status === "partially_reverted")
  ) {
    return run.run_id;
  }
  return "";
}

export function redoTargetForPath(
  path: string,
  revertedByRun: string,
  runs: ChangesetRunDto[],
): { runId: string; seq?: number } | null {
  const id = (revertedByRun || "").trim();
  if (!id) return null;
  const found = runs.find((r) => r.run_id === id);
  if (found && (found.status === "reverted" || found.archived)) return null;
  if (!found) return null;
  const entry = (found.entries || []).find(
    (e) => e.path === path && !e.reverted && (e.outcome ?? "ok") === "ok",
  );
  if (entry) return { runId: found.run_id, seq: entry.seq };
  const only = (found.entries || []).filter((e) => !e.reverted && (e.outcome ?? "ok") === "ok");
  if (only.length === 1) return { runId: found.run_id, seq: only[0].seq };
  return { runId: found.run_id };
}

export function rowRedoTarget(
  row: ChangeRow,
  runs: ChangesetRunDto[],
): { runId: string; seq?: number } | null {
  return redoTargetForPath(rowSlot(row), row.revertedByRun, runs);
}

export function formatSmartRevertBrief(args: {
  program: string;
  path: string;
  label: string;
  command?: string;
  reason?: string;
  laterWriter?: { name: string; runId: string; seq: number } | null;
}): string {
  const lines = [
    "Undo only this item, then compile if it is Verse.",
    `Program: ${programLabel(args.program)}`,
    `Target: ${args.label} (${args.path})`,
  ];
  if (args.command) lines.push(`Last command: ${args.command}`);
  if (args.reason) lines.push(`Why auto-revert cannot: ${args.reason}`);
  if (args.laterWriter) {
    lines.push(
      `Later writer: ${args.laterWriter.name} (run ${args.laterWriter.runId} seq ${args.laterWriter.seq}) — revert theirs first.`,
    );
  }
  return lines.join("\n");
}

export type ChangeSortKey = "time" | "name" | "kind" | "model";
export type ChangeSortDir = "asc" | "desc";

export function rowSortName(row: ChangeRow): string {
  if (row.kind === "file") {
    const path = row.path.replace(/\\/g, "/");
    return (path.split("/").pop() || path).toLowerCase();
  }
  return (row.label || row.slot || "").toLowerCase();
}

export function rowSortKind(row: ChangeRow): string {
  if (row.outcome !== "ok") return row.outcome;
  return row.kind === "file" ? "file" : (row.targetKind || "editor");
}

function clusterByKey(rows: ChangeRow[], keyOf: (row: ChangeRow) => string): ChangeRow[] {
  const buckets = new Map<string, ChangeRow[]>();
  const order: string[] = [];
  for (const row of rows) {
    const key = keyOf(row);
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = [];
      buckets.set(key, bucket);
      order.push(key);
    }
    bucket.push(row);
  }
  return order.flatMap((key) => buckets.get(key) || []);
}

/** Keep time order inside each program, but emit each program once (first seen). */
export function clusterRowsByProgram(rows: ChangeRow[]): ChangeRow[] {
  return clusterByKey(rows, programOfRow);
}

/** Program clusters, then kind clusters inside each program. */
export function clusterRowsByProgramAndKind(rows: ChangeRow[]): ChangeRow[] {
  const buckets = new Map<string, ChangeRow[]>();
  const order: string[] = [];
  for (const row of rows) {
    const program = programOfRow(row);
    let bucket = buckets.get(program);
    if (!bucket) {
      bucket = [];
      buckets.set(program, bucket);
      order.push(program);
    }
    bucket.push(row);
  }
  return order.flatMap((program) => clusterByKey(buckets.get(program) || [], rowSortKind));
}

/** Sort one run's rows. Time matches the default chronological timeline. */
export function sortChangeRows(rows: ChangeRow[], key: ChangeSortKey, dir: ChangeSortDir): ChangeRow[] {
  const sign = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    let cmp = 0;
    if (key === "name") cmp = rowSortName(a).localeCompare(rowSortName(b));
    else if (key === "kind") cmp = rowSortKind(a).localeCompare(rowSortKind(b));
    else cmp = a.firstTs - b.firstTs || a.firstSeq - b.firstSeq;
    return cmp === 0 ? (a.firstSeq - b.firstSeq) * sign : cmp * sign;
  });
}

/** Kind used when sorting run accordions (majority of that run's rows). */
export function runSortKind(run: ChangesetRunDto): string {
  const counts = new Map<string, number>();
  for (const row of runTimeline(run)) {
    const k = rowSortKind(row);
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  let best: string = run.status || "";
  let n = 0;
  for (const [k, c] of counts) {
    if (c > n || (c === n && k.localeCompare(best) < 0)) {
      best = k;
      n = c;
    }
  }
  return best;
}

export function runSortModel(run: ChangesetRunDto): string {
  return (run.model || "").trim().toLowerCase();
}

/**
 * Sort run accordions with the same Time / Kind / Name / Model keys as the rows inside.
 * Time asc keeps the ledger default: newest run on top. Model is a run-level key
 * (every row in a run shares the model), so row order stays chronological.
 */
export function sortRuns(
  runs: ChangesetRunDto[],
  key: ChangeSortKey,
  dir: ChangeSortDir,
  nameOf: (run: ChangesetRunDto) => string = (r) => (r.ducky_name || "").toLowerCase(),
): ChangesetRunDto[] {
  const sign = dir === "desc" ? -1 : 1;
  return [...runs].sort((a, b) => {
    let cmp = 0;
    if (key === "name") cmp = nameOf(a).localeCompare(nameOf(b));
    else if (key === "kind") cmp = runSortKind(a).localeCompare(runSortKind(b));
    else if (key === "model") cmp = runSortModel(a).localeCompare(runSortModel(b));
    else cmp = (b.started || 0) - (a.started || 0);
    if (cmp === 0) cmp = (b.started || 0) - (a.started || 0);
    return key === "time" ? (dir === "asc" ? cmp : -cmp) : cmp * sign;
  });
}
