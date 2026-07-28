import type { editor } from "monaco-editor";

import type { AskAiPayload } from "../../contexts/askAiHandlersRef";

let focusedEditor: editor.IStandaloneCodeEditor | null = null;
let focusedPath = "";

export function setFocusedAskAiEditor(ed: editor.IStandaloneCodeEditor | null, path: string): void {
  focusedEditor = ed;
  focusedPath = path;
}

export function readSelectionPayload(): AskAiPayload | null {
  const ed = focusedEditor;
  if (!ed) return null;
  const model = ed.getModel();
  if (!model || model.isDisposed()) return null;
  const selection = ed.getSelection();
  if (!selection || selection.isEmpty()) return null;
  const text = model.getValueInRange(selection).trim();
  if (!text) return null;
  return {
    text,
    filePath: focusedPath,
    startLine: selection.startLineNumber,
    endLine: selection.endLineNumber,
  };
}
