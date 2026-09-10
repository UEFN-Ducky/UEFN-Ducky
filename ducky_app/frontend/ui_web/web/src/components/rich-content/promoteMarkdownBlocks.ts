import type { RichBlock, RichCalloutTone, RichProgramKey } from "../../types/richContent";
import { inventoryKindFromLabel } from "./inventoryKind";

export type PromotedSegment =
  | { kind: "markdown"; text: string }
  | { kind: "block"; block: RichBlock };

const HEADER_CMD = /^\s*`([^`]+)`\s*$/;
const INVENTORY_ITEM =
  /^\s*[-*]\s+\*\*(.+?)\*\*\s*\/\s*`([^`]+)`\s*(?:[—–-]\s*(.*))?$/;
const H1 = /^#\s+(.*\S)\s*$/;
const H2 = /^##\s+(.*\S)\s*$/;
const BULLET = /^\s*[-*]\s+/;
const QUOTE = /^\s*>/;

function flushMarkdown(buf: string[], segs: PromotedSegment[]): void {
  const text = buf.join("\n").replace(/^\n+/, "").replace(/\n+$/, "");
  buf.length = 0;
  if (text.trim()) segs.push({ kind: "markdown", text });
}

function skipBlanks(lines: string[], i: number): number {
  while (i < lines.length && lines[i]!.trim() === "") i += 1;
  return i;
}

function firstInt(s: string): number | undefined {
  const m = s.match(/(\d+)/);
  return m ? Number(m[1]) : undefined;
}

function parsePrograms(s: string): Partial<Record<RichProgramKey, number>> {
  const out: Partial<Record<RichProgramKey, number>> = {};
  const re = /(uefn|blender|verse|file)\s*[:=]?\s*(\d+)/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    const key = m[1]!.toLowerCase() as RichProgramKey;
    out[key] = Number(m[2]);
  }
  return out;
}

function parseStatsBullets(bullets: string[]): Extract<RichBlock, { type: "stats" }> | null {
  let changes: number | undefined;
  let blocked: number | undefined;
  let programs: Partial<Record<RichProgramKey, number>> | undefined;
  for (const raw of bullets) {
    const text = raw.replace(BULLET, "").replace(/\*\*/g, "").trim();
    if (/program/i.test(text)) {
      const parsed = parsePrograms(text);
      if (Object.keys(parsed).length) programs = { ...programs, ...parsed };
      continue;
    }
    if (/block/i.test(text)) {
      blocked = firstInt(text);
      continue;
    }
    if (/change/i.test(text) || /applied/i.test(text)) {
      changes = firstInt(text);
    }
  }
  if (changes == null && blocked == null && !programs) return null;
  return { type: "stats", changes, blocked, programs };
}

function headingFolder(title: string): { heading: string; folder?: string } {
  const m = title.match(/^(.*?)(?:\s+[—–-]\s*`([^`]+)`\s*)?$/);
  const heading = (m?.[1] ?? title).trim();
  const folder = m?.[2]?.trim();
  return { heading, folder: folder || undefined };
}

function parseCalloutQuote(lines: string[]): Extract<RichBlock, { type: "callout" }> | null {
  const body = lines
    .map((ln) => ln.replace(/^\s*>\s?/, ""))
    .join("\n")
    .trim();
  if (!body) return null;
  const m =
    body.match(/^\*\*(loose end|warning|note):?\*\*\s*:?\s*([\s\S]*)$/i) ??
    body.match(/^(loose end|warning|note)\s*:?\s*([\s\S]*)$/i);
  if (!m) return null;
  const label = m[1]!.toLowerCase();
  const rest = (m[2] ?? "").trim() || body;
  const tone: RichCalloutTone = label === "note" ? "info" : "warn";
  const title =
    label === "loose end" ? "Loose End Remaining" : label === "warning" ? "Warning" : "Note";
  return { type: "callout", tone, title, text: rest };
}

/** Lift report-shaped markdown into the same widgets `ducky-rich` uses. */
export function promoteMarkdownToSegments(src: string): PromotedSegment[] {
  const lines = src.split("\n");
  const segs: PromotedSegment[] = [];
  const mdBuf: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i]!;

    const h1 = H1.exec(line);
    if (h1 && !line.startsWith("##")) {
      let j = skipBlanks(lines, i + 1);
      const cmd = j < lines.length ? HEADER_CMD.exec(lines[j]!) : null;
      if (cmd) {
        flushMarkdown(mdBuf, segs);
        segs.push({
          kind: "block",
          block: { type: "header", title: h1[1]!.trim(), command: cmd[1] },
        });
        i = j + 1;
        continue;
      }
    }

    const h2 = H2.exec(line);
    if (h2) {
      const title = h2[1]!.trim();
      if (/^run summary\b/i.test(title)) {
        let j = skipBlanks(lines, i + 1);
        const bullets: string[] = [];
        while (j < lines.length && BULLET.test(lines[j]!)) {
          bullets.push(lines[j]!);
          j += 1;
        }
        const stats = parseStatsBullets(bullets);
        if (stats) {
          flushMarkdown(mdBuf, segs);
          segs.push({ kind: "block", block: { type: "heading", level: 2, text: "Run Summary" } });
          segs.push({ kind: "block", block: stats });
          i = j;
          continue;
        }
      }

      if (/^inventory\b/i.test(title)) {
        const { heading, folder } = headingFolder(title);
        let j = skipBlanks(lines, i + 1);
        const items: Array<{ kind: string; title: string; desc?: string; label: string }> = [];
        const leftover: string[] = [];
        while (j < lines.length && BULLET.test(lines[j]!)) {
          const m = INVENTORY_ITEM.exec(lines[j]!);
          if (m) {
            items.push({
              kind: inventoryKindFromLabel(m[1]!),
              label: m[1]!.trim(),
              title: m[2]!.trim(),
              desc: (m[3] ?? "").trim() || undefined,
            });
          } else {
            leftover.push(lines[j]!);
          }
          j += 1;
        }
        if (items.length) {
          flushMarkdown(mdBuf, segs);
          segs.push({
            kind: "block",
            block: { type: "inventory", heading, folder, items },
          });
          if (leftover.length) mdBuf.push(...leftover);
          i = j;
          continue;
        }
      }
    }

    if (QUOTE.test(line)) {
      const quote: string[] = [];
      let j = i;
      while (j < lines.length && QUOTE.test(lines[j]!)) {
        quote.push(lines[j]!);
        j += 1;
      }
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
