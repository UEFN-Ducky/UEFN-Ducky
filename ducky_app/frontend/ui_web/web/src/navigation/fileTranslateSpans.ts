/**
 * Extract comment / string spans for visual file translate.
 * One small JSON batch → splice back. Never ask the model to rewrite the whole file.
 */

export type TranslateSpan = {
  /** Inclusive start of the replaceable inner text. */
  start: number;
  /** Exclusive end of the replaceable inner text. */
  end: number;
  /** Exact slice source[start:end] — what we send to the translator. */
  text: string;
};

const NON_TRANSLATABLE =
  /^(?:[\d\s.,:;!?'"“”‘’\-–—_/\\|@#%&+=*()[\]{}<>~`$^]+|https?:\/\/\S+|\/?[\w.-]+(?:\/[\w./-]*)+)$/;
const IDENTIFIER_LIKE =
  /^(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[a-z]+[A-Z][A-Za-z0-9]*)$/;

export function isTranslatablePhrase(text: string): boolean {
  const t = text.replace(/\s+/g, " ").trim();
  if (!t || t.length < 2) return false;
  if (NON_TRANSLATABLE.test(t)) return false;
  // Short code tokens only when there's no whitespace (allow "OK", "Hi", …).
  if (!/\s/.test(t) && /^[A-Za-z0-9_.-]{1,3}$/.test(t)) return false;
  if (IDENTIFIER_LIKE.test(t)) return false;
  try {
    if (!/\p{L}/u.test(t)) return false;
  } catch {
    if (!/[A-Za-z\u00C0-\u024F]/.test(t)) return false;
  }
  return true;
}

/** Scan source for line comments, block comments, and string literals. */
export function extractTranslateSpans(source: string): TranslateSpan[] {
  const out: TranslateSpan[] = [];
  const n = source.length;
  let i = 0;
  while (i < n) {
    const c = source[i];
    const next = i + 1 < n ? source[i + 1] : "";

    // Line comment //
    if (c === "/" && next === "/") {
      const bodyStart = i + 2;
      let j = bodyStart;
      while (j < n && source[j] !== "\n" && source[j] !== "\r") j++;
      const text = source.slice(bodyStart, j);
      if (isTranslatablePhrase(text)) out.push({ start: bodyStart, end: j, text });
      i = j;
      continue;
    }

    // Block comment /* */
    if (c === "/" && next === "*") {
      const bodyStart = i + 2;
      let j = bodyStart;
      while (j + 1 < n && !(source[j] === "*" && source[j + 1] === "/")) j++;
      const bodyEnd = j;
      const text = source.slice(bodyStart, bodyEnd);
      if (isTranslatablePhrase(text)) out.push({ start: bodyStart, end: bodyEnd, text });
      i = j + 2 < n ? j + 2 : n;
      continue;
    }

    // Double-quoted string
    if (c === '"') {
      let j = i + 1;
      while (j < n) {
        if (source[j] === "\\") {
          j += 2;
          continue;
        }
        if (source[j] === '"') break;
        j++;
      }
      const bodyStart = i + 1;
      const bodyEnd = j;
      if (j < n) {
        const text = source.slice(bodyStart, bodyEnd);
        if (isTranslatablePhrase(text)) out.push({ start: bodyStart, end: bodyEnd, text });
        i = j + 1;
      } else {
        i = n;
      }
      continue;
    }

    i++;
  }
  return out;
}

/** Unique phrase list (stable order of first appearance). */
export function uniqueSpanTexts(spans: TranslateSpan[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const s of spans) {
    if (seen.has(s.text)) continue;
    seen.add(s.text);
    out.push(s.text);
  }
  return out;
}

/** Apply translations to spans (end→start so offsets stay valid). */
export function applyTranslateSpans(
  source: string,
  spans: TranslateSpan[],
  map: Record<string, string>,
): string {
  if (!spans.length) return source;
  const ordered = [...spans].sort((a, b) => b.start - a.start);
  let out = source;
  for (const span of ordered) {
    const repl = map[span.text];
    if (typeof repl !== "string" || !repl) continue;
    // Keep roughly same shape — don't inject newlines into a one-line comment body.
    const safe =
      span.text.indexOf("\n") < 0 && repl.indexOf("\n") >= 0
        ? repl.replace(/\s*\n\s*/g, " ").trim()
        : repl;
    out = out.slice(0, span.start) + safe + out.slice(span.end);
  }
  return out;
}

/** True when map didn't change any span text (model echoed English). */
export function translationMapIsNoop(
  phrases: string[],
  map: Record<string, string> | undefined,
): boolean {
  if (!phrases.length) return true;
  if (!map) return true;
  let any = false;
  for (const p of phrases) {
    const v = map[p];
    if (typeof v === "string" && v.trim() && v !== p) {
      any = true;
      break;
    }
  }
  return !any;
}

/** Chunk phrases for translate_ui_batch (max 40). */
export function chunkPhrases(phrases: string[], size = 40): string[][] {
  const out: string[][] = [];
  for (let i = 0; i < phrases.length; i += size) out.push(phrases.slice(i, i + size));
  return out;
}
