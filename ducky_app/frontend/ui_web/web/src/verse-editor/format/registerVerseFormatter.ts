/**
 * Wires the standalone {@link formatVerseDocument} formatter into Monaco as the
 * "verse" document-formatting provider. This is a PURE CLIENT-SIDE formatter — it does
 * not talk to verse-lsp (which ships no formatting capability), so "Format Document"
 * works even when the language server is down or still loading.
 *
 * Registration is idempotent and happens once from `setupMonaco` (memoized), so the
 * provider outlives LSP reconnects instead of leaking a new provider on every connect.
 */
import type * as Monaco from "monaco-editor";

import { formatVerseDocument, DEFAULT_VERSE_FORMAT_OPTIONS } from "./verseFormatter";
import { verseEditorLog, verseEditorLogError } from "../verseEditorLog";

let registered = false;

export function registerVerseFormatter(monaco: typeof Monaco): void {
  if (registered) return;
  registered = true;

  monaco.languages.registerDocumentFormattingEditProvider("verse", {
    displayName: "Verse",
    provideDocumentFormattingEdits(model, formattingOptions) {
      try {
        const original = model.getValue();
        const formatted = formatVerseDocument(original, {
          indentSize: formattingOptions.tabSize || DEFAULT_VERSE_FORMAT_OPTIONS.indentSize,
          indentChar: formattingOptions.insertSpaces ? " " : "\t",
        });
        // No change → return no edits so the undo stack and cursor are left alone.
        if (formatted === original) return [];
        verseEditorLog("format", "formatted document", {
          uri: model.uri.toString(),
          before: original.length,
          after: formatted.length,
        });
        // A single full-range replace is applied through Monaco's edit stack, so
        // Format Document stays undoable (unlike model.setValue, which wipes undo).
        return [{ range: model.getFullModelRange(), text: formatted }];
      } catch (e) {
        verseEditorLogError("format", "formatting failed", e);
        return [];
      }
    },
  });

  verseEditorLog("format", "registered verse document formatter");
}
