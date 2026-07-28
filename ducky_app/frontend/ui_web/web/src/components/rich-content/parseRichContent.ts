import type { ParsedRichContent, RichBlock, RichEnvelope } from "../../types/richContent";

const DUCKY_RICH_FENCE = /```ducky-rich\s*\n([\s\S]*?)```/i;

function isRichBlock(value: unknown): value is RichBlock {
  if (!value || typeof value !== "object") return false;
  const t = (value as { type?: string }).type;
  return typeof t === "string" && t.length > 0;
}

function isRichEnvelope(value: unknown): value is RichEnvelope {
  if (!value || typeof value !== "object") return false;
  const env = value as RichEnvelope;
  return env.__rich === true && Array.isArray(env.blocks) && env.blocks.every(isRichBlock);
}

function parseJsonEnvelope(text: string): ParsedRichContent | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (isRichEnvelope(parsed)) {
      return { kind: "blocks", blocks: parsed.blocks, summary: parsed.summary };
    }
    if (Array.isArray(parsed) && parsed.every(isRichBlock)) {
      return { kind: "blocks", blocks: parsed };
    }
  } catch {
    return null;
  }
  return null;
}

function parseDuckyRichFence(text: string): ParsedRichContent | null {
  const match = DUCKY_RICH_FENCE.exec(text);
  if (!match) return null;
  const inner = match[1]?.trim();
  if (!inner) return null;
  try {
    const parsed = JSON.parse(inner) as unknown;
    if (isRichEnvelope(parsed)) {
      return { kind: "blocks", blocks: parsed.blocks, summary: parsed.summary };
    }
    if (Array.isArray(parsed) && parsed.every(isRichBlock)) {
      return { kind: "blocks", blocks: parsed };
    }
  } catch {
    return null;
  }
  return null;
}

/** Detect markdown vs structured rich blocks in assistant text. */
export function parseRichContent(text: string): ParsedRichContent {
  const trimmed = text.trim();
  if (!trimmed) return { kind: "markdown", text: "" };

  const envelope = parseJsonEnvelope(trimmed);
  if (envelope) return envelope;

  const fenced = parseDuckyRichFence(trimmed);
  if (fenced) return fenced;

  return { kind: "markdown", text };
}
