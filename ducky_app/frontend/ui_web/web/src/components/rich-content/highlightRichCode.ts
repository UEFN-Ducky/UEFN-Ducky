export type RichCodeKind = "comment" | "string" | "number" | "kw" | "fn";
export type RichCodeSpan = { text: string; kind?: RichCodeKind };

const KEYWORDS = new Set([
  "if", "else", "return", "import", "from", "def", "class", "true", "false", "none",
  "and", "or", "not", "in", "const", "let", "var", "function", "for", "while", "with",
  "as", "pass", "try", "except", "raise", "new", "this", "await", "async",
]);

/** Cheap token paint for chat fences. Not a full language grammar. */
export function highlightRichCode(src: string): RichCodeSpan[] {
  const out: RichCodeSpan[] = [];
  const push = (text: string, kind?: RichCodeKind) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push(kind ? { text, kind } : { text });
  };
  let i = 0;
  while (i < src.length) {
    if (src[i] === "#" || src.startsWith("//", i)) {
      const end = src.indexOf("\n", i);
      const n = end === -1 ? src.length : end;
      push(src.slice(i, n), "comment");
      i = n;
      continue;
    }
    const q = src[i];
    if (q === '"' || q === "'" || q === "`") {
      let j = i + 1;
      while (j < src.length && src[j] !== q) {
        j += src[j] === "\\" ? 2 : 1;
      }
      push(src.slice(i, Math.min(j + 1, src.length)), "string");
      i = Math.min(j + 1, src.length);
      continue;
    }
    if (/[0-9]/.test(src[i]!) && (i === 0 || !/[\w]/.test(src[i - 1]!))) {
      let j = i;
      while (j < src.length && /[0-9.]/.test(src[j]!)) j += 1;
      push(src.slice(i, j), "number");
      i = j;
      continue;
    }
    if (/[A-Za-z_]/.test(src[i]!)) {
      let j = i;
      while (j < src.length && /[\w.]/.test(src[j]!)) j += 1;
      const word = src.slice(i, j);
      let k = j;
      while (k < src.length && src[k] === " ") k += 1;
      if (src[k] === "(") push(word, "fn");
      else if (KEYWORDS.has(word.toLowerCase())) push(word, "kw");
      else push(word);
      i = j;
      continue;
    }
    push(src[i]!);
    i += 1;
  }
  return out;
}
