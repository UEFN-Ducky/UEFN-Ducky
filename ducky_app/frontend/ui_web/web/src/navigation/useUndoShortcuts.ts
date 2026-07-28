import { useEffect } from "react";

/** Focus contexts that own their own undo (text editing) — VS Code lets those handle
 * Ctrl+Z themselves, so our file/chat undo must stay out of the way. */
function isTextEditingContext(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if ((el as HTMLElement).isContentEditable) return true;
  if (el.closest(".monaco-editor")) return true; // Monaco has its own undo stack
  return false;
}

/** Whether Ctrl+Z should drive the app-level (file/chat) undo, matching VS Code's
 * focus-scoping. It fires when focus is inside an `[data-undo-scope]` region (the file
 * tree or chat sidebar) — and also when nothing in particular is focused (`<body>`),
 * which is where focus lands right after a delete removes the focused row. It does NOT
 * fire in a text editor/input (those own Ctrl+Z) or when some other chrome element (a
 * header button, etc.) has focus. */
function undoScopeAllows(el: Element | null): boolean {
  if (!el || el === document.body) return true;
  if (isTextEditingContext(el)) return false;
  return !!el.closest("[data-undo-scope]");
}

/** Ctrl/Cmd+Z → undo, Ctrl/Cmd+Shift+Z or Ctrl+Y → redo, scoped like VS Code. */
export function useUndoShortcuts(undo: () => void, redo: () => void): void {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod || e.altKey) return;
      const key = e.key.toLowerCase();
      const isUndo = key === "z" && !e.shiftKey;
      const isRedo = (key === "z" && e.shiftKey) || key === "y";
      if (!isUndo && !isRedo) return;
      if (!undoScopeAllows(document.activeElement)) return;
      e.preventDefault();
      if (isUndo) undo();
      else redo();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undo, redo]);
}
