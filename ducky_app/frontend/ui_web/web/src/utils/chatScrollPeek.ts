import type { ChatTurn } from "./chatMessageGroups";

const PEEK_CHARS = 72;

/** Collapse markdown/whitespace so the peek card stays one line per field. */
export function flattenPeekText(text: string, max = PEEK_CHARS): string {
  const flat = (text || "").replace(/[`*_#>\[\]()]/g, " ").replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  return `${flat.slice(0, Math.max(1, max - 1))}…`;
}

export type ChatScrollPeekLines = {
  query: string;
  reply: string;
  more: string;
};

export function peekLinesForTurn(turn: ChatTurn): ChatScrollPeekLines {
  const query =
    turn.query?.kind === "bubble" && turn.query.role === "user"
      ? flattenPeekText(turn.query.text)
      : "";
  const texts: string[] = [];
  for (const row of turn.responses) {
    if (row.kind === "bubble" && row.text.trim()) {
      texts.push(flattenPeekText(row.text));
    } else if (row.kind === "bubble" && row.thinking?.trim()) {
      texts.push(flattenPeekText(row.thinking));
    } else if (row.kind === "tool") {
      texts.push(flattenPeekText(row.intent.tool?.name || "tool"));
    } else if (row.kind === "activity") {
      texts.push(row.items.length === 1 ? "1 tool" : `${row.items.length} tools`);
    }
    if (texts.length >= 2) break;
  }
  return { query, reply: texts[0] ?? "", more: texts[1] ?? "" };
}

/** Content Y of each turn, splitting measured chunk heights evenly across their turns. */
export function turnOffsetsFromChunkHeights(
  heights: readonly number[],
  turnsPerChunk: number,
  turnCount: number,
): number[] {
  const offsets: number[] = [];
  let y = 0;
  for (let t = 0; t < turnCount; t++) {
    offsets.push(y);
    const chunk = Math.floor(t / turnsPerChunk);
    const inChunk = Math.min(turnsPerChunk, turnCount - chunk * turnsPerChunk);
    const h = heights[chunk] ?? 0;
    y += inChunk > 0 ? h / inChunk : 0;
  }
  return offsets;
}

export function indexAtOffset(offsets: readonly number[], y: number): number {
  if (offsets.length === 0) return -1;
  let i = 0;
  while (i < offsets.length - 1 && offsets[i + 1] <= y) i++;
  return i;
}

export function indexAtFraction(frac: number, count: number): number {
  if (count <= 0) return -1;
  const f = Math.min(1, Math.max(0, frac));
  return Math.min(count - 1, Math.floor(f * count));
}

export type PeekTickLayout = {
  indexes: number[];
  start: number;
  gap: number;
  stackH: number;
};

/** Short dashes packed at a fixed gap and centered — never stretched to fill the pane. */
export function peekTickLayout(
  count: number,
  trackPx: number,
  gapPx = 3,
): PeekTickLayout {
  if (count <= 0 || trackPx <= 0) return { indexes: [], start: 0, gap: 0, stackH: 0 };
  const maxFit = Math.max(1, Math.floor(trackPx / gapPx) + 1);
  const n = Math.min(count, maxFit);
  const indexes: number[] = [];
  for (let s = 0; s < n; s++) {
    const i = n === 1 ? 0 : Math.round((s / (n - 1)) * (count - 1));
    if (indexes.length === 0 || indexes[indexes.length - 1] !== i) indexes.push(i);
  }
  const shown = indexes.length;
  const stackH = Math.min(trackPx, Math.max(0, (shown - 1) * gapPx));
  const gap = shown > 1 ? stackH / (shown - 1) : 0;
  return { indexes, start: (trackPx - stackH) / 2, gap, stackH };
}
