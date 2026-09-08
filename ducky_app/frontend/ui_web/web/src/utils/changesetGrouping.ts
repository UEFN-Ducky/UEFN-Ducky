import type { ChangesetConflictDto, ChangesetEntryDto, ChangesetRunDto } from "../types/panel";

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

/** One row per path: writes to the same file collapse to the latest state. */
export function groupChangesetEntries(run: ChangesetRunDto): ChangesetFileRow[] {
  const byPath = new Map<string, ChangesetFileRow>();
  const entries = [...(run.entries || [])].sort((a, b) => a.seq - b.seq);
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

export interface ChangesetRunSummary {
  files: number;
  entries: number;
  conflicts: number;
  outOfLane: number;
  reverted: boolean;
  partiallyReverted: boolean;
  running: boolean;
}

export function changesetRunSummary(run: ChangesetRunDto): ChangesetRunSummary {
  const rows = groupChangesetEntries(run);
  const entries = run.entries || [];
  return {
    files: rows.length,
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
