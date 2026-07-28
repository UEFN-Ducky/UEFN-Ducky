import type { editor } from "monaco-editor";
import type { EditorActionStyle } from "../queue/types";

const STYLES: Record<EditorActionStyle, editor.IModelDecorationOptions> = {
  selection: {
    className: "verse-editor-highlight-selection",
    isWholeLine: true,
  },
  delete: {
    className: "verse-editor-highlight-delete",
    isWholeLine: true,
    inlineClassName: "verse-editor-strikethrough",
  },
  insert: {
    className: "verse-editor-highlight-insert",
    isWholeLine: true,
  },
  agent_cursor: {
    className: "verse-editor-highlight-cursor",
    isWholeLine: true,
  },
};

export function applyDecoration(
  ed: editor.IStandaloneCodeEditor,
  style: EditorActionStyle,
  range: { startLine: number; startCol: number; endLine: number; endCol: number },
): string[] {
  const model = ed.getModel();
  if (!model) return [];
  const monacoRange = {
    startLineNumber: range.startLine,
    startColumn: range.startCol,
    endLineNumber: Math.max(range.endLine, range.startLine),
    endColumn: range.endCol,
  };
  return ed.deltaDecorations(
    [],
    [{ range: monacoRange, options: STYLES[style] }],
  );
}

export function clearAllDecorations(ed: editor.IStandaloneCodeEditor, ids: string[]): void {
  if (ids.length) ed.deltaDecorations(ids, []);
}
