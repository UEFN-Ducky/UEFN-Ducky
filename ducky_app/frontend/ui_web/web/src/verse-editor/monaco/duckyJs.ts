import type * as MonacoNs from "monaco-editor";

/**
 * JavaScript for workflow Custom code nodes.
 *
 * The `ducky` object's types come from the backend as a module declaration
 * (`declare module "ducky" { ... }`) and are added to Monaco's JavaScript
 * service as extra libs. Only module-scoped declarations are added: a global
 * one would leak into every JavaScript file opened in the IDE. The type check
 * (checkJsModel) is asked for these models only and nothing global changes, so
 * type errors show where the code opts in with `// @ts-check`.
 */
type Monaco = typeof MonacoNs;

export type CodeMarker = {
  line: number;
  col?: number;
  endLine?: number;
  endCol?: number;
  message: string;
  severity: "error" | "warning" | "info";
};

/** Marker owner for problems this app reports (the check, the last run, Test). */
export const DUCKY_MARKER_OWNER = "ducky-code";

const installed = new Map<string, { content: string; dispose: () => void }>();

export function isModuleScoped(dts: string): boolean {
  return /\bdeclare\s+module\s+["'][^"']+["']/.test(dts);
}

/** Add (or replace) the module-scoped declarations; anything global is left out. */
export function setDuckyLibs(monaco: Monaco, libs: string[]): void {
  const wanted = new Map<string, string>();
  libs.filter((dts) => dts && isModuleScoped(dts)).forEach((dts, index) => wanted.set(`file:///node_modules/@types/ducky-workflow-${index}/index.d.ts`, dts));
  for (const [path, lib] of installed) {
    if (wanted.get(path) === lib.content) continue;
    try { lib.dispose(); } catch { /* already gone */ }
    installed.delete(path);
  }
  for (const [path, content] of wanted) {
    if (installed.has(path)) continue;
    const handle = monaco.languages.typescript.javascriptDefaults.addExtraLib(content, path);
    installed.set(path, { content, dispose: () => handle.dispose() });
  }
}

/** One model per workflow node, under a path no project file can have. */
export function codeModelUri(monaco: Monaco, path: string): MonacoNs.Uri {
  const clean = path.split("/").map((part) => encodeURIComponent(part)).join("/");
  return monaco.Uri.parse(`file:///ducky-workflow-code/${clean}.js`);
}

export function toMonacoMarkers(monaco: Monaco, model: MonacoNs.editor.ITextModel, markers: CodeMarker[]): MonacoNs.editor.IMarkerData[] {
  const lines = model.getLineCount();
  return markers.filter((marker) => Number.isFinite(marker.line) && marker.line >= 1).map((marker) => {
    // Out-of-range lines land on the last line rather than vanishing.
    const line = Math.min(lines, Math.floor(marker.line));
    const endLine = Math.min(lines, Math.max(line, Math.floor(marker.endLine ?? line)));
    const lineEnd = model.getLineMaxColumn(endLine);
    const col = Math.min(model.getLineMaxColumn(line), Math.max(1, Math.floor(marker.col ?? 1)));
    // A column alone underlines from there to the end of the line.
    const endCol = Math.min(lineEnd, Math.floor(marker.endCol ?? lineEnd));
    return {
      severity: marker.severity === "error" ? monaco.MarkerSeverity.Error : marker.severity === "warning" ? monaco.MarkerSeverity.Warning : monaco.MarkerSeverity.Info,
      message: marker.message,
      startLineNumber: line,
      startColumn: col,
      endLineNumber: endLine,
      endColumn: endLine === line && endCol <= col ? col + 1 : endCol,
    };
  });
}

/** Marker owner for the type check of these models (JavaScript's semantic check is off in
 *  Monaco by default, and turning it on globally would change every JS file in the IDE). */
export const DUCKY_JS_CHECK_OWNER = "ducky-js-check";

type TsDiagnostic = { start?: number; length?: number; messageText: string | { messageText: string; next?: unknown[] }; category: number };

function flattenMessage(text: TsDiagnostic["messageText"]): string {
  if (typeof text === "string") return text;
  const parts = [text.messageText];
  for (const next of (text.next || []) as TsDiagnostic["messageText"][]) parts.push(flattenMessage(next));
  return parts.join(" ");
}

/** The TypeScript service's semantic check of one model: what `// @ts-check` reports. */
export async function checkJsModel(monaco: Monaco, model: MonacoNs.editor.ITextModel): Promise<void> {
  const getWorker = await monaco.languages.typescript.getJavaScriptWorker();
  const worker = await getWorker(model.uri);
  const found = await worker.getSemanticDiagnostics(model.uri.toString()) as TsDiagnostic[];
  if (model.isDisposed()) return;
  monaco.editor.setModelMarkers(model, DUCKY_JS_CHECK_OWNER, found.filter((item) => typeof item.start === "number").map((item) => {
    const start = model.getPositionAt(item.start!);
    const end = model.getPositionAt(item.start! + (item.length || 1));
    return {
      // TypeScript's categories: 0 warning, 1 error, 2 suggestion, 3 message.
      severity: item.category === 1 ? monaco.MarkerSeverity.Error : item.category === 0 ? monaco.MarkerSeverity.Warning : monaco.MarkerSeverity.Info,
      message: flattenMessage(item.messageText),
      startLineNumber: start.lineNumber,
      startColumn: start.column,
      endLineNumber: end.lineNumber,
      endColumn: end.column,
    };
  }));
}

/** Markers Monaco's own JavaScript check put on a model (not this app's). */
export function serviceMarkers(monaco: Monaco, model: MonacoNs.editor.ITextModel): CodeMarker[] {
  return monaco.editor.getModelMarkers({ resource: model.uri }).filter((marker) => marker.owner !== DUCKY_MARKER_OWNER).map((marker) => ({
    line: marker.startLineNumber,
    col: marker.startColumn,
    endLine: marker.endLineNumber,
    endCol: marker.endColumn,
    message: marker.message,
    severity: marker.severity >= monaco.MarkerSeverity.Error ? "error" : marker.severity >= monaco.MarkerSeverity.Warning ? "warning" : "info",
  }));
}
