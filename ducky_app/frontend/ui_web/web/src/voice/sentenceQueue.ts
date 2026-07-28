/**
 * Incremental text → speakable sentences.
 * Strips fenced code / inline code so TTS never reads brackets aloud.
 */

const SENTENCE_END = /([.!?…]+)(\s+|$)/;

/** Strip markdown noise that should not be spoken. */
export function stripForSpeech(raw: string): string {
  let text = raw || "";
  // Fenced code blocks
  text = text.replace(/```[\s\S]*?```/g, " ");
  // Inline code
  text = text.replace(/`[^`]+`/g, " ");
  // Links: keep label
  text = text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  // Images
  text = text.replace(/!\[[^\]]*\]\([^)]+\)/g, " ");
  // Headings / bold / italic markers
  text = text.replace(/^#{1,6}\s+/gm, "");
  text = text.replace(/(\*\*|__)(.*?)\1/g, "$2");
  text = text.replace(/(\*|_)(.*?)\1/g, "$2");
  // Collapse internal whitespace; keep leading/trailing spaces so streamed
  // word boundaries ("Hello " + "world") stay intact until a sentence closes.
  return text.replace(/[^\S\n]+/g, " ");
}

export type SentenceFlush = { sentences: string[]; remainder: string };

/**
 * Pull complete sentences out of a growing buffer.
 * On ``force``, leftover remainder becomes one final sentence if non-empty.
 */
export function pullSentences(buffer: string, force = false): SentenceFlush {
  const clean = stripForSpeech(buffer);
  if (!clean.trim()) return { sentences: [], remainder: "" };

  const sentences: string[] = [];
  let rest = clean;
  let match: RegExpExecArray | null;
  while ((match = SENTENCE_END.exec(rest)) !== null) {
    const punctEnd = match.index + match[1].length;
    const piece = rest.slice(0, punctEnd).replace(/\s+/g, " ").trim();
    if (piece) sentences.push(piece);
    // Consume trailing whitespace after the terminator so the next word starts clean.
    rest = rest.slice(match.index + match[0].length);
  }

  if (force) {
    const leftover = rest.replace(/\s+/g, " ").trim();
    if (leftover) sentences.push(leftover);
    return { sentences, remainder: "" };
  }

  // Also emit long clause chunks ending in newline-ish pauses (already collapsed).
  // Keep short incomplete tails in remainder.
  if (rest.length > 280 && /[,;:]\s/.test(rest)) {
    const lastBreak = Math.max(rest.lastIndexOf("; "), rest.lastIndexOf(", "), rest.lastIndexOf(": "));
    if (lastBreak > 80) {
      const piece = rest.slice(0, lastBreak + 1).trim();
      if (piece) sentences.push(piece);
      rest = rest.slice(lastBreak + 1).trimStart();
    }
  }

  return { sentences, remainder: rest };
}

/** Mutable queue used by ttsEngine. */
export class SentenceQueue {
  private buffer = "";
  private pending: string[] = [];

  enqueue(delta: string): string[] {
    if (!delta) return [];
    this.buffer += delta;
    const { sentences, remainder } = pullSentences(this.buffer, false);
    this.buffer = remainder;
    if (sentences.length) this.pending.push(...sentences);
    const out = this.pending.slice();
    this.pending = [];
    return out;
  }

  flush(): string[] {
    const { sentences, remainder } = pullSentences(this.buffer, true);
    this.buffer = remainder;
    const out = [...this.pending, ...sentences];
    this.pending = [];
    return out;
  }

  clear(): void {
    this.buffer = "";
    this.pending = [];
  }
}
