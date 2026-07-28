import type { ReactNode } from "react";
import { createElement } from "react";

const KEYWORDS = new Set([
  "False", "None", "True", "and", "as", "assert", "async", "await", "break",
  "class", "continue", "def", "del", "elif", "else", "except", "finally", "for",
  "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
  "or", "pass", "raise", "return", "try", "while", "with", "yield",
]);

const BUILTINS = new Set([
  "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
  "len", "list", "map", "max", "min", "print", "range", "set", "str", "sum",
  "tuple", "type", "zip", "open", "isinstance", "hasattr", "getattr", "setattr",
]);

type TokenKind = "comment" | "string" | "keyword" | "builtin" | "number" | "decorator" | "plain";

interface Token {
  kind: TokenKind;
  text: string;
}

function isIdentStart(ch: string): boolean {
  return /[A-Za-z_]/.test(ch);
}

function isIdentChar(ch: string): boolean {
  return /[A-Za-z0-9_]/.test(ch);
}

function readString(code: string, quoteIdx: number): number {
  const quote = code[quoteIdx];
  const triple = code.slice(quoteIdx, quoteIdx + 3) === quote.repeat(3);
  let i = quoteIdx + (triple ? 3 : 1);
  while (i < code.length) {
    if (code[i] === "\\") {
      i += 2;
      continue;
    }
    if (triple) {
      if (code.slice(i, i + 3) === quote.repeat(3)) return i + 3;
    } else if (code[i] === quote) {
      return i + 1;
    }
    i += 1;
  }
  return code.length;
}

/** Find start of a Python string at i (handles r/f/b/u prefixes). Returns quote index or -1. */
function findStringQuote(code: string, i: number): number {
  if (code[i] === "'" || code[i] === '"') return i;
  if (i > 0 && isIdentChar(code[i - 1]!)) return -1;

  let j = i;
  while (j < code.length && j - i < 3 && "frbuFRBU".includes(code[j]!)) j += 1;
  if (j > i && j < code.length && (code[j] === "'" || code[j] === '"')) return j;
  return -1;
}

/** Lightweight Python tokenizer for tool-card previews. */
export function tokenizePython(code: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;

  while (i < code.length) {
    const ch = code[i]!;

    if (ch === "#") {
      let end = code.indexOf("\n", i);
      if (end < 0) end = code.length;
      tokens.push({ kind: "comment", text: code.slice(i, end) });
      i = end;
      continue;
    }

    const quoteIdx = findStringQuote(code, i);
    if (quoteIdx >= 0) {
      const end = readString(code, quoteIdx);
      tokens.push({ kind: "string", text: code.slice(i, end) });
      i = end;
      continue;
    }

    if (ch === "@" && (i === 0 || /\s/.test(code[i - 1]!))) {
      let end = i + 1;
      while (end < code.length && (isIdentChar(code[end]!) || code[end] === ".")) end += 1;
      tokens.push({ kind: "decorator", text: code.slice(i, end) });
      i = end;
      continue;
    }

    if (/[0-9]/.test(ch)) {
      let end = i + 1;
      while (end < code.length && /[0-9xXa-fA-F._]/.test(code[end]!)) end += 1;
      tokens.push({ kind: "number", text: code.slice(i, end) });
      i = end;
      continue;
    }

    if (isIdentStart(ch)) {
      let end = i + 1;
      while (end < code.length && isIdentChar(code[end]!)) end += 1;
      const word = code.slice(i, end);
      tokens.push({
        kind: KEYWORDS.has(word) ? "keyword" : BUILTINS.has(word) ? "builtin" : "plain",
        text: word,
      });
      i = end;
      continue;
    }

    let end = i + 1;
    while (end < code.length) {
      const c = code[end]!;
      if (c === "#" || c === "'" || c === '"' || c === "@") break;
      if (/[0-9]/.test(c) || isIdentStart(c)) break;
      end += 1;
    }
    tokens.push({ kind: "plain", text: code.slice(i, end) });
    i = end;
  }

  return tokens;
}

const KIND_CLASS: Record<TokenKind, string | null> = {
  comment: "tool-py-comment",
  string: "tool-py-string",
  keyword: "tool-py-keyword",
  builtin: "tool-py-builtin",
  number: "tool-py-number",
  decorator: "tool-py-decorator",
  plain: null,
};

export function renderHighlightedPython(code: string): ReactNode[] {
  return tokenizePython(code).map((tok, idx) => {
    const cls = KIND_CLASS[tok.kind];
    if (!cls) return tok.text;
    return createElement("span", { key: idx, className: cls }, tok.text);
  });
}
