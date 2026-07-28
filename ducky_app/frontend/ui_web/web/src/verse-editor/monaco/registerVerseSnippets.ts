import { parse } from "jsonc-parser";
import type { languages } from "monaco-editor";
import snippetRaw from "../lang/verse-snippets.json?raw";
import { verseEditorLog, verseEditorLogError, verseEditorError } from "../verseEditorLog";

type SnippetEntry = {
  prefix?: string | string[];
  body?: string | string[];
  description?: string;
};

function loadSnippets(): SnippetEntry[] {
  try {
    const parsed = parse(snippetRaw) as Record<string, SnippetEntry>;
    const entries = Object.values(parsed).filter((s) => s.prefix && s.body);
    verseEditorLog("snippets", "loaded", { count: entries.length });
    return entries;
  } catch (err) {
    verseEditorLogError("snippets", "failed to parse verse-snippets.json", err);
    return [];
  }
}

export function registerVerseSnippets(monaco: typeof import("monaco-editor")): void {
  const snippets = loadSnippets();
  if (!snippets.length) {
    verseEditorError("snippets", "no snippets registered — Epic snippet file missing or invalid");
    return;
  }

  monaco.languages.registerCompletionItemProvider("verse", {
    provideCompletionItems: (model, position) => {
      try {
        const linePrefix = model.getValueInRange({
          startLineNumber: position.lineNumber,
          startColumn: 1,
          endLineNumber: position.lineNumber,
          endColumn: position.column,
        });
        const word = linePrefix.match(/[\w-]*$/)?.[0] ?? "";
        // Member access ("Foo.S") — snippets are top-level constructs; offering
        // `set`/`struct` after a dot buries the LSP member list in noise.
        if (linePrefix[linePrefix.length - word.length - 1] === ".") {
          return { suggestions: [] };
        }
        const range = {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: position.column - word.length,
          endColumn: position.column,
        };
        const items: languages.CompletionItem[] = snippets.flatMap((snippet) => {
          const prefixes = Array.isArray(snippet.prefix) ? snippet.prefix : [snippet.prefix!];
          const body = Array.isArray(snippet.body) ? snippet.body.join("\n") : (snippet.body ?? "");
          return prefixes.map((prefix) => ({
            label: prefix,
            kind: monaco.languages.CompletionItemKind.Snippet,
            insertText: body,
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            detail: snippet.description,
            documentation: snippet.description,
            range,
            filterText: prefix,
          }));
        });
        return { suggestions: items };
      } catch (err) {
        verseEditorLogError("snippets", "provideCompletionItems failed", err);
        return { suggestions: [] };
      }
    },
  });
}
