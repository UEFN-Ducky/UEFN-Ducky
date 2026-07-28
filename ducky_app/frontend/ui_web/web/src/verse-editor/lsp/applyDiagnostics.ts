import type { editor } from "monaco-editor";

import type { LspDiagnostic } from "./verseLspClient";



export const VERSE_LSP_MARKER_OWNER = "verse-lsp";
const MARKER_OWNER = VERSE_LSP_MARKER_OWNER;



function lspSeverityToMonaco(

  monaco: typeof import("monaco-editor"),

  severity?: number,

): number {

  switch (severity) {

    case 2:

      return monaco.MarkerSeverity.Warning;

    case 3:

      return monaco.MarkerSeverity.Info;

    case 4:

      return monaco.MarkerSeverity.Hint;

    default:

      return monaco.MarkerSeverity.Error;

  }

}



function normalizeMarkerRange(

  monaco: typeof import("monaco-editor"),

  d: LspDiagnostic,

): editor.IMarkerData {

  const startLine = d.range.start.line + 1;

  const startCol = d.range.start.character + 1;

  let endLine = d.range.end.line + 1;

  let endCol = d.range.end.character + 1;



  if (endLine < startLine || (endLine === startLine && endCol <= startCol)) {

    endCol = startCol + 1;

  }



  return {

    severity: lspSeverityToMonaco(monaco, d.severity),

    message: d.message,

    startLineNumber: startLine,

    startColumn: startCol,

    endLineNumber: endLine,

    endColumn: endCol,

  };

}



export function applyLspDiagnostics(

  monaco: typeof import("monaco-editor"),

  model: editor.ITextModel,

  diagnostics: LspDiagnostic[],

): void {

  if (model.isDisposed()) return;

  const markers: editor.IMarkerData[] = diagnostics.map((d) => normalizeMarkerRange(monaco, d));

  monaco.editor.setModelMarkers(model, MARKER_OWNER, markers);

}



export function clearLspDiagnostics(monaco: typeof import("monaco-editor"), model: editor.ITextModel): void {

  if (model.isDisposed()) return;

  monaco.editor.setModelMarkers(model, MARKER_OWNER, []);

}

/** Restore squiggles from registry items (1-based line/column) with ZERO LSP traffic —
 * VS Code-style marker persistence when a tab regains focus / the editor remounts. */
export function applyStoredDiagnostics(
  monaco: typeof import("monaco-editor"),
  model: editor.ITextModel,
  items: Array<{ line: number; column: number; message: string; severity: "error" | "warning" }>,
): void {
  if (model.isDisposed()) return;
  const markers: editor.IMarkerData[] = items.map((item) => ({
    severity:
      item.severity === "warning" ? monaco.MarkerSeverity.Warning : monaco.MarkerSeverity.Error,
    message: item.message,
    startLineNumber: item.line,
    startColumn: item.column,
    endLineNumber: item.line,
    endColumn: item.column + 1,
  }));
  monaco.editor.setModelMarkers(model, MARKER_OWNER, markers);
}

/** Read current verse-lsp Monaco squiggles as LSP diagnostics (Problems must match the buffer). */
export function readVerseLspMarkersAsDiagnostics(
  monaco: typeof import("monaco-editor"),
  model: editor.ITextModel,
): LspDiagnostic[] {
  if (model.isDisposed()) return [];
  const markers = monaco.editor.getModelMarkers({ resource: model.uri, owner: MARKER_OWNER });
  const out: LspDiagnostic[] = [];
  for (const m of markers) {
    if (
      m.severity !== monaco.MarkerSeverity.Error &&
      m.severity !== monaco.MarkerSeverity.Warning
    ) {
      continue;
    }
    out.push({
      message: m.message,
      severity: m.severity === monaco.MarkerSeverity.Warning ? 2 : 1,
      range: {
        start: { line: Math.max(0, m.startLineNumber - 1), character: Math.max(0, m.startColumn - 1) },
        end: { line: Math.max(0, m.endLineNumber - 1), character: Math.max(0, m.endColumn - 1) },
      },
    });
  }
  return out;
}

