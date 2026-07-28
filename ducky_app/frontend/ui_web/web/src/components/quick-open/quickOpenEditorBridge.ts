import type { editor } from "monaco-editor";

import { buildOutlineTree, flattenOutlineSymbols, type FlatOutlineSymbol } from "../../verse-editor/outline/outlineSymbolIcons";
import { resolveOpenDocUri } from "../../verse-editor/lsp/resolveOpenDocUri";
import { getVerseLspSession } from "../../verse-editor/lsp/verseLspSession";
import { openQuickOpenFromEditor, type QuickOpenLaunchOptions } from "./quickOpenGlobal";

const BLOCKED_COMMAND_IDS = new Set([
  "editor.action.quickCommand",
  "editor.action.quickOutline",
  "editor.action.gotoLine",
]);

let focusedEditor: editor.IStandaloneCodeEditor | null = null;
let focusedPath = "";

export function setQuickOpenEditor(ed: editor.IStandaloneCodeEditor | null, path: string): void {
  focusedEditor = ed;
  focusedPath = path;
}

export function clearQuickOpenEditor(ed: editor.IStandaloneCodeEditor, path: string): void {
  if (focusedEditor === ed && focusedPath === path) {
    focusedEditor = null;
    focusedPath = "";
  }
}

export function getQuickOpenEditor(): { editor: editor.IStandaloneCodeEditor; path: string } | null {
  if (!focusedEditor) return null;
  const model = focusedEditor.getModel();
  if (!model || model.isDisposed()) return null;
  return { editor: focusedEditor, path: focusedPath };
}

export function listEditorCommands(): { id: string; label: string }[] {
  const ctx = getQuickOpenEditor();
  if (!ctx) return [];

  const seen = new Set<string>();
  return ctx.editor
    .getSupportedActions()
    .filter((action) => action.isSupported() && action.label && !BLOCKED_COMMAND_IDS.has(action.id))
    .filter((action) => {
      const key = action.label.trim().toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((action) => ({ id: action.id, label: action.label }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function runEditorCommand(id: string): void {
  const ctx = getQuickOpenEditor();
  if (!ctx) return;
  try {
    ctx.editor.focus();
    void ctx.editor.getAction(id)?.run();
  } catch {
    /* editor disposed during tab switch */
  }
}

/** Route Monaco's palette keybindings to the unified quick-open (VS Code parity). */
export function installQuickOpenKeybindings(
  ed: editor.IStandaloneCodeEditor,
  monacoApi: typeof import("monaco-editor"),
): void {
  const open = (opts: QuickOpenLaunchOptions) => () => openQuickOpenFromEditor(opts);
  ed.addCommand(monacoApi.KeyCode.F1, open({ mode: "command", query: ">" }));
  ed.addCommand(
    monacoApi.KeyMod.CtrlCmd | monacoApi.KeyMod.Shift | monacoApi.KeyCode.KeyP,
    open({ mode: "command", query: ">" }),
  );
  ed.addCommand(
    monacoApi.KeyMod.CtrlCmd | monacoApi.KeyMod.Shift | monacoApi.KeyCode.KeyO,
    open({ mode: "symbol", query: "@" }),
  );
  ed.addCommand(monacoApi.KeyMod.CtrlCmd | monacoApi.KeyCode.KeyP, open({ mode: "file" }));
}

export function revealSymbolLine(line: number): void {
  const ctx = getQuickOpenEditor();
  if (!ctx) return;
  try {
    const column = 1;
    ctx.editor.setPosition({ lineNumber: line, column });
    ctx.editor.revealLineInCenter(line);
    ctx.editor.focus();
  } catch {
    /* editor disposed */
  }
}

export async function fetchQuickOpenSymbols(): Promise<FlatOutlineSymbol[]> {
  const ctx = getQuickOpenEditor();
  if (!ctx) return [];

  const session = getVerseLspSession();
  if (!session?.client.isConnected()) return [];

  const resolved = resolveOpenDocUri(session.projectRoot, ctx.path);
  if (!resolved) return [];

  try {
    const raw = await session.client.getDocumentSymbols(resolved.docUri);
    return flattenOutlineSymbols(buildOutlineTree(raw));
  } catch {
    return [];
  }
}
