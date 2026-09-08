import type { ChangesetConflictDto, ChangesetEntryDto, ChangesetRunDto } from "../types/panel";

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
  const parts = slot.replace(/^uefn:\/\//, "").split("/");
  return parts[1] || parts[0] || slot;
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
    const row = bySlot.get(e.path);
    if (!row) {
      bySlot.set(e.path, {
        kind: "editor",
        key: `editor:${e.path}`,
        slot: e.path,
        command: editor.command || e.tool,
        verb: editorVerb(editor.command || e.tool, editor.facet || "", created),
        targetKind: editor.kind || "other",
        facet: editor.facet || "",
        label: slotLabel(e.path, e),
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
    reverted: run.status === "reverted",
    partiallyReverted: run.status === "partially_reverted",
    running: run.status === "running",
  };
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
