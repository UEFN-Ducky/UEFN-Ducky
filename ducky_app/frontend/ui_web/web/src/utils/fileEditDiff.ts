export type FileDiffLine =
  | { kind: "context"; line: number; text: string }
  | { kind: "add"; line: number; text: string }
  | { kind: "remove"; line: number; text: string };

export interface FileDiffHunk {
  id: string;
  lines: FileDiffLine[];
}

export interface FileEditDiffSummary {
  path: string;
  fileName: string;
  linesAdded: number;
  linesRemoved: number;
  hunks: FileDiffHunk[];
  isCreate: boolean;
}

const CONTEXT_LINES = 2;

function fileNameFromPath(path: string): string {
  const norm = path.replace(/\\/g, "/");
  const parts = norm.split("/");
  return parts[parts.length - 1] || path;
}

/** Build hunks with limited context — Cursor-style collapsed diff regions. */
export function buildFileEditDiff(
  path: string,
  before: string,
  after: string,
  linesAdded: number,
  linesRemoved: number,
  kind: "write" | "create" = "write",
): FileEditDiffSummary {
  const beforeLines = before.split("\n");
  const afterLines = after.split("\n");
  const n = beforeLines.length;
  const m = afterLines.length;

  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      if (beforeLines[i] === afterLines[j]) dp[i][j] = dp[i + 1][j + 1] + 1;
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const raw: FileDiffLine[] = [];
  let i = 0;
  let j = 0;
  let afterLine = 1;
  let beforeLine = 1;
  while (i < n && j < m) {
    if (beforeLines[i] === afterLines[j]) {
      raw.push({ kind: "context", line: afterLine, text: afterLines[j] });
      i += 1;
      j += 1;
      afterLine += 1;
      beforeLine += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ kind: "remove", line: beforeLine, text: beforeLines[i] });
      i += 1;
      beforeLine += 1;
    } else {
      raw.push({ kind: "add", line: afterLine, text: afterLines[j] });
      j += 1;
      afterLine += 1;
    }
  }
  while (i < n) {
    raw.push({ kind: "remove", line: beforeLine, text: beforeLines[i] });
    i += 1;
    beforeLine += 1;
  }
  while (j < m) {
    raw.push({ kind: "add", line: afterLine, text: afterLines[j] });
    j += 1;
    afterLine += 1;
  }

  const changeIdx = raw
    .map((line, idx) => (line.kind === "context" ? -1 : idx))
    .filter((idx) => idx >= 0);
  const keep = new Set<number>();
  for (const idx of changeIdx) {
    for (let k = Math.max(0, idx - CONTEXT_LINES); k <= Math.min(raw.length - 1, idx + CONTEXT_LINES); k += 1) {
      keep.add(k);
    }
  }
  const filtered = raw.filter((_, idx) => keep.has(idx));

  const hunks: FileDiffHunk[] = [];
  let current: FileDiffLine[] = [];
  let hunkId = 0;
  for (const line of filtered) {
    if (line.kind === "context" && current.length > 0 && current[current.length - 1]?.kind !== "context") {
      hunks.push({ id: `hunk-${hunkId}`, lines: current });
      hunkId += 1;
      current = [line];
      continue;
    }
    current.push(line);
  }
  if (current.length > 0) {
    hunks.push({ id: `hunk-${hunkId}`, lines: current });
  }

  return {
    path,
    fileName: fileNameFromPath(path),
    linesAdded,
    linesRemoved,
    hunks: kind === "create" ? [{ id: "hunk-0", lines: afterLines.map((text, idx) => ({ kind: "add" as const, line: idx + 1, text })) }] : hunks,
    isCreate: kind === "create",
  };
}
