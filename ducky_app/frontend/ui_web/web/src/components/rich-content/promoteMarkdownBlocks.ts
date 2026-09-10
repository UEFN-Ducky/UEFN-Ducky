import type { RichBlock, RichCalloutTone, RichProgramKey } from "../../types/richContent";
import { inventoryKindFromLabel } from "./inventoryKind";

export type PromotedSegment =
  | { kind: "markdown"; text: string }
  | { kind: "block"; block: RichBlock };

const HEADER_CMD = /^`([^`]+)`\s*$/;
const INVENTORY_ITEM = /^[-*+]\s+\*\*(.+?)\*\*\s*\/\s*`([^`]+)`\s*(?:[—–-]\s*(.*))?$/;
const H1 = /^#\s+(.*\S)\s*$/;
const H2 = /^##\s+(.*\S)\s*$/;
const BULLET = /^[-*+]\s+/;
const QUOTE = /^>\s?/;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;

function flushMarkdown(buf: string[], segs: PromotedSegment[]): void {
  const text = buf.join("\n").replace(/^\n+|\n+$/g, "");
  buf.length = 0;
  if (text.trim()) segs.push({ kind: "markdown", text });
}

function skipBlanks(lines: string[], i: number): number {
  while (i < lines.length && !lines[i]!.trim()) i += 1;
  return i;
}

/** Read only a flat list. Nested lists/paragraphs remain ordinary Markdown. */
function readBullets(lines: string[], start: number): { bullets: string[]; end: number } {
  const bullets: string[] = [];
  let end = skipBlanks(lines, start);
  while (end < lines.length && BULLET.test(lines[end]!)) {
    bullets.push(lines[end]!);
    end += 1;
    const next = skipBlanks(lines, end);
    if (BULLET.test(lines[next] ?? "")) end = next;
  }
  // Do not detach a continuation, indented code or a nested list from its parent.
  if (/^\s+\S/.test(lines[skipBlanks(lines, end)] ?? "")) return { bullets: [], end: start };
  if ((lines[end] ?? "").trim() && !/^(?:#|>)/.test(lines[end]!)) return { bullets: [], end: start };
  return { bullets, end };
}

function parseStatsBullets(bullets: string[]): Extract<RichBlock, { type: "stats" }> | null {
  const stats: Extract<RichBlock, { type: "stats" }> = { type: "stats" };
  for (const raw of bullets) {
    const text = raw.replace(BULLET, "").replace(/\*\*/g, "").trim();
    const metric = /^(editor changes|changes|blocked(?: ops)?):\s*(\d[\d,]*)(?:\s+(applied|retries))?$/i.exec(text);
    if (metric) {
      const count = Number(metric[2]!.replace(/,/g, ""));
      const key = /^blocked/i.test(metric[1]!) ? "blocked" : "changes";
      if (!Number.isSafeInteger(count) || stats[key] != null) return null;
      stats[key] = count;
      continue;
    }
    const programs = /^programs:\s*(.+)$/i.exec(text);
    if (!programs || stats.programs) return null;
    const parts = programs[1]!.split(/\s*[·,;|]\s*/);
    const counts: Partial<Record<RichProgramKey, number>> = {};
    for (const part of parts) {
      const m = /^(uefn|blender|verse|file)\s*[:=]?\s*(\d+)$/i.exec(part);
      if (!m) return null;
      const key = m[1]!.toLowerCase() as RichProgramKey;
      const count = Number(m[2]);
      if (!Number.isSafeInteger(count) || counts[key] != null) return null;
      counts[key] = count;
    }
    stats.programs = counts;
  }
  return Object.keys(stats).length > 1 ? stats : null;
}

const CALLOUTS: Record<string, { tone: RichCalloutTone; title: string }> = {
  note: { tone: "info", title: "Note" },
  info: { tone: "info", title: "Note" },
  tip: { tone: "info", title: "Tip" },
  important: { tone: "warn", title: "Important" },
  warning: { tone: "warn", title: "Warning" },
  caution: { tone: "warn", title: "Caution" },
  gotcha: { tone: "warn", title: "Gotcha" },
  "loose end": { tone: "warn", title: "Loose End Remaining" },
  blocked: { tone: "warn", title: "Blocked" },
  error: { tone: "error", title: "Error" },
  success: { tone: "success", title: "Success" },
  verified: { tone: "success", title: "Verified" },
};

function parseCalloutQuote(lines: string[]): Extract<RichBlock, { type: "callout" }> | null {
  const body = lines.map((line) => line.replace(QUOTE, "")).join("\n").trim();
  const alert = /^\[!(\w+)\][ \t]*([^\n]*)(?:\n([\s\S]*))?$/.exec(body);
  if (alert) {
    const config = CALLOUTS[alert[1]!.toLowerCase()];
    if (!config) return null;
    return { type: "callout", ...config, title: alert[2]?.trim() || config.title, text: (alert[3] ?? "").trim() };
  }
  const label = body.match(/^\*\*([a-z ]+):?\*\*\s*:?\s*([\s\S]*)$/i)
    ?? body.match(/^([a-z ]+):\s*([\s\S]*)$/i);
  const config = label && CALLOUTS[label[1]!.toLowerCase()];
  return config ? { type: "callout", ...config, text: label![2]!.trim() } : null;
}

/** Promote explicit semantic Markdown only. Never infer completed work or counts. */
export function promoteMarkdownToSegments(src: string): PromotedSegment[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const segs: PromotedSegment[] = [];
  const mdBuf: string[] = [];
  let fence: string | undefined;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const marker = FENCE.exec(line)?.[1];
    if (fence || marker) {
      // Examples inside fenced code are never report widgets, even mid-stream.
      if (fence) {
        if (marker?.[0] === fence[0] && marker.length >= fence.length && !line.trim().slice(marker.length).trim()) fence = undefined;
      } else {
        fence = marker;
      }
      mdBuf.push(line);
      i += 1;
      continue;
    }

    const h1 = H1.exec(line);
    if (h1) {
      const j = skipBlanks(lines, i + 1);
      const cmd = HEADER_CMD.exec(lines[j] ?? "");
      // A heading itself is enough; a command chip is optional.
      flushMarkdown(mdBuf, segs);
      segs.push({ kind: "block", block: { type: "header", title: h1[1]!.trim(), command: cmd?.[1] } });
      i = cmd ? j + 1 : i + 1;
      continue;
    }

    const h2 = H2.exec(line);
    if (h2) {
      const title = h2[1]!.trim();
      if (/^run summary$/i.test(title)) {
        const { bullets, end } = readBullets(lines, i + 1);
        const stats = parseStatsBullets(bullets);
        if (stats) {
          flushMarkdown(mdBuf, segs);
          segs.push({ kind: "block", block: { type: "heading", level: 2, text: title } });
          segs.push({ kind: "block", block: stats });
          i = end;
          continue;
        }
      }
      if (/^(inventory\b|(?:created|updated) assets\b)/i.test(title)) {
        const { bullets, end } = readBullets(lines, i + 1);
        const matches = bullets.map((bullet) => INVENTORY_ITEM.exec(bullet));
        // Mixed lists stay intact: no dropped/reordered bullets or lost caveats.
        if (matches.length && matches.every((m) => m !== null)) {
          const folder = /\s+[—–-]\s*`([^`]+)`\s*$/.exec(title);
          flushMarkdown(mdBuf, segs);
          segs.push({ kind: "block", block: {
            type: "inventory",
            heading: folder ? title.slice(0, folder.index).trim() : title,
            folder: folder?.[1],
            items: matches.map((m) => ({
              kind: inventoryKindFromLabel(m![1]!), label: m![1]!.trim(),
              title: m![2]!.trim(), desc: m![3]?.trim() || undefined,
            })),
          } });
          i = end;
          continue;
        }
      }
    }

    if (QUOTE.test(line)) {
      let j = i;
      const quote: string[] = [];
      while (j < lines.length && QUOTE.test(lines[j]!)) quote.push(lines[j++]!);
      const callout = parseCalloutQuote(quote);
      if (callout) {
        flushMarkdown(mdBuf, segs);
        segs.push({ kind: "block", block: callout });
        i = j;
        continue;
      }
    }
    mdBuf.push(line);
    i += 1;
  }
  flushMarkdown(mdBuf, segs);
  return segs.length ? segs : [{ kind: "markdown", text: src }];
}
