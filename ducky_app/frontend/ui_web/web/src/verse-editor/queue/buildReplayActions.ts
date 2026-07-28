import type { FileEditData } from "../../types/panel";
import type { EditorAction, EditorActionStyle, EditorRange } from "./types";

const MAX_HUNKS = 12;

/** One navigable stop in a stepped replay: scroll to `scrollLine`, then highlight
 * `range` with `style`. Consumed by ReplayController for the play/prev/next overlay. */
export interface ReplayStep {
  scrollLine: number;
  range: EditorRange;
  style: EditorActionStyle;
  kind: "insert" | "delete";
}

interface DiffHunk {
  /** After-content coordinates (the buffer being decorated holds the post-edit text). */
  startLine: number;
  endLine: number;
  kind: "insert" | "delete";
}

/** LCS opcode walk (same DP as buildFileEditDiff) collapsed into after-coordinate
 * hunks: added runs decorate their lines, pure deletions mark the boundary line. */
function diffHunks(before: string, after: string): DiffHunk[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;

  const dp: number[][] = Array.from({ length: n + 1 }, () => Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const hunks: DiffHunk[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      i += 1;
      j += 1;
      continue;
    }
    let removed = 0;
    let added = 0;
    const addStart = j + 1;
    while (i < n || j < m) {
      if (i < n && j < m && a[i] === b[j]) break;
      if (i < n && (j >= m || dp[i + 1][j] >= dp[i][j + 1])) {
        i += 1;
        removed += 1;
      } else {
        j += 1;
        added += 1;
      }
    }
    if (added > 0) {
      hunks.push({ startLine: addStart, endLine: addStart + added - 1, kind: "insert" });
    } else if (removed > 0) {
      hunks.push({ startLine: Math.max(1, Math.min(addStart, m)), endLine: Math.max(1, Math.min(addStart, m)), kind: "delete" });
    }
    if (hunks.length >= MAX_HUNKS) break;
  }
  return hunks;
}

/** Replay a past agent edit as a visual walkthrough on the CURRENT buffer:
 * open beside the chat, scroll to each change, highlight it. Deliberately no
 * apply_content — replay must never clobber edits made since. `force` makes it
 * play even when follow-code is disabled or speed is instant. */
export function buildReplayActions(edit: FileEditData): EditorAction[] {
  const path = (edit.path || "").trim().replace(/\\/g, "/");
  if (!path) return [];

  const hunks =
    edit.kind === "create"
      ? [{ startLine: 1, endLine: Math.max(1, edit.after.split("\n").length), kind: "insert" as const }]
      : diffHunks(edit.before, edit.after);

  const actions: EditorAction[] = [{ type: "open_file", path, force: true, durationMs: 400 }];
  for (const hunk of hunks) {
    actions.push({
      type: "scroll_to",
      path,
      range: { startLine: hunk.startLine, startCol: 1, endLine: hunk.startLine, endCol: 1 },
      durationMs: 250,
      force: true,
    });
    actions.push({
      type: "highlight",
      path,
      range: { startLine: hunk.startLine, startCol: 1, endLine: hunk.endLine, endCol: 1 },
      style: hunk.kind,
      durationMs: 700,
      force: true,
    });
  }
  if (hunks.length === 0) {
    // Unchanged write: still show the file and mark line 1 briefly.
    actions.push({
      type: "highlight",
      path,
      range: { startLine: 1, startCol: 1, endLine: 1, endCol: 1 },
      style: "agent_cursor",
      durationMs: 500,
      force: true,
    });
  }
  actions.push({ type: "clear_decorations", path, force: true });
  return actions;
}

/** Same hunk analysis as buildReplayActions, but shaped as discrete, user-navigable
 * steps for the ReplayController (play/pause/prev/next). A "create" replays as one
 * step spanning the whole new file; an unchanged write yields zero steps. */
export function buildReplaySteps(edit: FileEditData): { path: string; steps: ReplayStep[] } {
  const path = (edit.path || "").trim().replace(/\\/g, "/");
  if (!path) return { path: "", steps: [] };

  const hunks =
    edit.kind === "create"
      ? [{ startLine: 1, endLine: Math.max(1, edit.after.split("\n").length), kind: "insert" as const }]
      : diffHunks(edit.before, edit.after);

  // "insert"/"delete" are valid EditorActionStyle values (see monaco/decorations).
  const steps: ReplayStep[] = hunks.map((h) => ({
    scrollLine: h.startLine,
    range: { startLine: h.startLine, startCol: 1, endLine: h.endLine, endCol: 1 },
    style: h.kind,
    kind: h.kind,
  }));

  return { path, steps };
}
