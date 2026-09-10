import type { ReactNode } from "react";
import { createElement } from "react";

type TokenKind = "string" | "keyword" | "number" | "plain";

interface Token {
  kind: TokenKind;
  text: string;
}

/** Lightweight JSON tokenizer so diffs use the same colors as Python tool cards. */
export function tokenizeJson(code: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < code.length) {
    const ch = code[i]!;
    if (ch === '"') {
      let end = i + 1;
      while (end < code.length) {
        if (code[end] === "\\") {
          end += 2;
          continue;
        }
        if (code[end] === '"') {
          end += 1;
          break;
        }
        end += 1;
      }
      tokens.push({ kind: "string", text: code.slice(i, end) });
      i = end;
      continue;
    }
    if (/[0-9-]/.test(ch) && (ch !== "-" || /[0-9]/.test(code[i + 1] ?? ""))) {
      let end = i + 1;
      while (end < code.length && /[0-9.eE+-]/.test(code[end]!)) end += 1;
      tokens.push({ kind: "number", text: code.slice(i, end) });
      i = end;
      continue;
    }
    if (isIdentStart(ch)) {
      let end = i + 1;
      while (end < code.length && /[A-Za-z]/.test(code[end]!)) end += 1;
      const word = code.slice(i, end);
      tokens.push({
        kind: word === "true" || word === "false" || word === "null" ? "keyword" : "plain",
        text: word,
      });
      i = end;
      continue;
    }
    let end = i + 1;
    while (end < code.length) {
      const c = code[end]!;
      if (c === '"' || /[0-9-]/.test(c) || isIdentStart(c)) break;
      end += 1;
    }
    tokens.push({ kind: "plain", text: code.slice(i, end) });
    i = end;
  }
  return tokens;
}

function isIdentStart(ch: string): boolean {
  return /[A-Za-z]/.test(ch);
}

const KIND_CLASS: Record<TokenKind, string | null> = {
  string: "tool-py-string",
  keyword: "tool-py-keyword",
  number: "tool-py-number",
  plain: null,
};

export function renderHighlightedJson(code: string): ReactNode[] {
  return tokenizeJson(code).map((tok, idx) => {
    const cls = KIND_CLASS[tok.kind];
    if (!cls) return tok.text;
    return createElement("span", { key: idx, className: cls }, tok.text);
  });
}
