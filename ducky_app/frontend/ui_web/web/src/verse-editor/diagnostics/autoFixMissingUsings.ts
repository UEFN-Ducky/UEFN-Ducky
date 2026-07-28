import type { editor } from "monaco-editor";

import type { LspDiagnostic } from "../lsp/verseLspClient";
import { verseLspLog } from "../lsp/verseLspDebug";

const MISSING_USING_RE = /Did you forget to specify using\s*\{\s*(\/[^}\s]+)\s*\}/i;
const ACTIVE_USING_RE = /^\s*using\s*\{\s*(\/[^}]+)\s*\}/;

/** Namespaces verse-lsp says are missing from the file's using block. */
export function extractMissingUsingNamespaces(diagnostics: LspDiagnostic[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const d of diagnostics) {
    const match = d.message.match(MISSING_USING_RE);
    if (!match) continue;
    const ns = match[1].trim();
    if (seen.has(ns)) continue;
    seen.add(ns);
    out.push(ns);
  }
  return out;
}

export function fileHasActiveUsing(source: string, namespace: string): boolean {
  for (const line of source.split(/\r?\n/)) {
    const match = line.match(ACTIVE_USING_RE);
    if (match && match[1].trim() === namespace) return true;
  }
  return false;
}

function detectEol(source: string): string {
  return source.includes("\r\n") ? "\r\n" : "\n";
}

function lastActiveUsingLineIndex(lines: string[]): number {
  let last = -1;
  for (let i = 0; i < lines.length; i++) {
    if (ACTIVE_USING_RE.test(lines[i])) last = i;
  }
  return last;
}

/** Insert `using { /… }` lines immediately after the last active using (or at file top). */
export function buildMissingUsingInsert(
  source: string,
  namespaces: string[],
): { text: string; line: number } | null {
  const toAdd = namespaces.filter((ns) => !fileHasActiveUsing(source, ns));
  if (toAdd.length === 0) return null;

  const eol = detectEol(source);
  const lines = source.split(/\r?\n/);
  const lastUsingIdx = lastActiveUsingLineIndex(lines);
  const newLines = toAdd.map((ns) => `using { ${ns} }`);
  const text = newLines.join(eol) + eol;
  const line = lastUsingIdx >= 0 ? lastUsingIdx + 2 : 1;
  return { text, line };
}

/** Auto-insert missing `using` imports reported by verse-lsp. Returns true when edited. */
export function tryAutoFixMissingUsings(
  model: editor.ITextModel,
  diagnostics: LspDiagnostic[],
): boolean {
  if (model.isDisposed()) return false;

  const namespaces = extractMissingUsingNamespaces(diagnostics);
  if (namespaces.length === 0) return false;

  const source = model.getValue();
  const insert = buildMissingUsingInsert(source, namespaces);
  if (!insert) return false;

  model.pushEditOperations(
    [],
    [
      {
        range: {
          startLineNumber: insert.line,
          startColumn: 1,
          endLineNumber: insert.line,
          endColumn: 1,
        },
        text: insert.text,
      },
    ],
    () => null,
  );

  verseLspLog("diagnostics", "auto-inserted missing using", { namespaces: insert.text.trim() });
  return true;
}
