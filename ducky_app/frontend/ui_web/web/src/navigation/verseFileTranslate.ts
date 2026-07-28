/**
 * Helpers for Verse visual full-file translate (chunk + quality gate + cache keys).
 * One-shot whole-file rewrites fail on local models (~5k chars → a few keywords).
 * Per-chunk content cache avoids re-spending tokens when a file only changes partly.
 */

/** Soft cap per LLM call — small enough for Ollama to actually rewrite. */
export const VERSE_TRANSLATE_CHUNK_CHARS = 1_600;

/** Compact system prompt — resent every chunk; keep short. */
export const VERSE_TRANSLATE_SYSTEM =
  "Visual localizer for source. Return ONLY the translated chunk — no fences, no commentary.\n" +
  "Translate EVERY English word (identifiers, keywords, comments, strings). " +
  "Keep punctuation, braces, indentation, numbers, and paths like /Verse.org/… unchanged. " +
  "Same line count. Visual aid only (not compiled). " +
  "Example (Bulgarian): using→използване; SubscribeAgent→АбонирайАгент; wrapper_agent→обвивка_агент.";

export function stripTranslateFences(text: string): string {
  let raw = String(text || "").trim();
  if (raw.startsWith("```")) {
    raw = raw.replace(/^```(?:verse|versepath|text|[\w-]*)?\s*/i, "").replace(/\s*```\s*$/, "");
  }
  return raw;
}

/** Matches LanguagesTab clear: vf_<lang>_* / vc_<lang>_* */
export function verseLangSlug(lang: string): string {
  return lang.trim().toLowerCase().replace(/[^\w]+/g, "").slice(0, 12) || "lang";
}

export function verseTranslateCacheKey(lang: string, digest: string): string {
  return `vf_${verseLangSlug(lang)}_${digest.slice(0, 40)}`;
}

/** Content-addressed chunk cache — survives path renames / partial file edits. */
export function verseChunkCacheKey(lang: string, digest: string): string {
  return `vc_${verseLangSlug(lang)}_${digest.slice(0, 40)}`;
}

/** Split on line boundaries so the model never sees a 5k-char wall. */
export function splitIntoLineChunks(text: string, maxChars = VERSE_TRANSLATE_CHUNK_CHARS): string[] {
  if (!text) return [""];
  if (text.length <= maxChars) return [text];
  const parts = text.split(/(?<=\n)/);
  const chunks: string[] = [];
  let buf = "";
  for (const part of parts) {
    if (buf && buf.length + part.length > maxChars) {
      chunks.push(buf);
      buf = part;
    } else {
      buf += part;
    }
  }
  if (buf) chunks.push(buf);
  return chunks.length ? chunks : [text];
}

function englishCodeWords(text: string): string[] {
  return (text.match(/\b[A-Za-z][A-Za-z0-9_]{2,}\b/g) || []).map((w) => w.toLowerCase());
}

/**
 * Skip LLM when the chunk has nothing to translate (blank / paths-only).
 * Module paths still contain letters — strip them first.
 */
export function chunkNeedsLlm(chunk: string): boolean {
  const stripped = chunk
    .replace(/\/[\w.]+(?:\/[\w./-]*)*/g, " ")
    .replace(/https?:\/\/\S+/gi, " ");
  return englishCodeWords(stripped).length > 0;
}

/**
 * True when the model barely changed the English (lazy keyword swap / echo).
 * Char-alignment checks miss "using→използване" then leave the rest English.
 */
export function looksLikeWeakTranslation(source: string, translated: string): boolean {
  const a = source.replace(/\s+/g, " ").trim();
  const b = translated.replace(/\s+/g, " ").trim();
  if (!b) return true;
  if (a === b) return true;

  if (b.length < a.length * 0.4 || b.length > a.length * 3.5) return true;

  const srcLines = source.split(/\r?\n/).length;
  const dstLines = translated.split(/\r?\n/).length;
  if (srcLines >= 5 && (dstLines < srcLines * 0.45 || dstLines > srcLines * 2.2)) return true;

  const srcWords = englishCodeWords(source);
  const unique = [...new Set(srcWords)];
  if (unique.length < 8) {
    const n = Math.min(a.length, b.length);
    if (n < 20) return a === b;
    let same = 0;
    for (let i = 0; i < n; i++) if (a[i] === b[i]) same++;
    return same / n > 0.85;
  }

  const dstSet = new Set(englishCodeWords(translated));
  let retained = 0;
  for (const w of unique) if (dstSet.has(w)) retained++;
  // >55% of distinct English tokens still present → model did almost nothing.
  return retained / unique.length > 0.55;
}
