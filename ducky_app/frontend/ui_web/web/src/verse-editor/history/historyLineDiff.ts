export type HistoryDiffSegment =
  | { kind: "same"; currentLine: number; text: string }
  | { kind: "remove"; currentLine: number; text: string }
  | { kind: "add"; afterLine: number; text: string };

/** Line-based LCS diff: current (editor) vs historical (saved snapshot). */
export function diffLineHistory(currentText: string, historicalText: string): HistoryDiffSegment[] {
  const current = currentText.split("\n");
  const historical = historicalText.split("\n");
  const n = current.length;
  const m = historical.length;

  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      if (current[i] === historical[j]) dp[i][j] = dp[i + 1][j + 1] + 1;
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const out: HistoryDiffSegment[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (current[i] === historical[j]) {
      out.push({ kind: "same", currentLine: i + 1, text: current[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: "remove", currentLine: i + 1, text: current[i] });
      i += 1;
    } else {
      const afterLine = i;
      out.push({ kind: "add", afterLine, text: historical[j] });
      j += 1;
    }
  }
  while (i < n) {
    out.push({ kind: "remove", currentLine: i + 1, text: current[i] });
    i += 1;
  }
  while (j < m) {
    out.push({ kind: "add", afterLine: n, text: historical[j] });
    j += 1;
  }
  return out;
}

export function countHistoryDiffChanges(segments: HistoryDiffSegment[]): number {
  return segments.filter((s) => s.kind !== "same").length;
}
