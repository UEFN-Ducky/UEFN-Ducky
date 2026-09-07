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

/** Tick indexes that fit on the track without stacking, always including `active`. */
export function visibleTickIndexes(count: number, trackPx: number, active: number, minGap = 5): number[] {
  if (count <= 0 || trackPx <= 0) return [];
  const out: number[] = [];
  let lastY = -minGap;
  for (let i = 0; i < count; i++) {
    const y = (i / Math.max(1, count - 1)) * trackPx;
    if (i !== active && y - lastY < minGap) continue;
    out.push(i);
    lastY = y;
  }
  if (active >= 0 && active < count && !out.includes(active)) {
    out.push(active);
    out.sort((a, b) => a - b);
  }
  return out;
}
