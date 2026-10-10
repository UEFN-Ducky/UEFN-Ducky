import { useEffect } from "react";

/** Focus contexts that own their own undo (text editing) — VS Code lets those handle
 * Ctrl+Z themselves, so tree undo must stay out of the way. */
function isTextEditingContext(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if ((el as HTMLElement).isContentEditable) return true;
  if (el.closest(".monaco-editor")) return true; // Monaco has its own undo stack
  return false;
}

/**
 * The tree whose history Ctrl+Z drives, VS Code style: only the tree (Duckies, Content
 * or Workflows) that has focus — the one the person last clicked or pressed in. Never
 * from a chat, the composer, an editor, a text field, another panel, or when nothing
 * is focused.
 */
export function undoScopeFor(el: Element | null): string | null {
  if (!el || el === document.body || el === document.documentElement) return null;
  if (isTextEditingContext(el)) return null;
  const host = el.closest("[data-undo-scope]");
  return host?.getAttribute("data-undo-scope") || null;
}

/** Ctrl/Cmd+Z → undo, Ctrl/Cmd+Shift+Z or Ctrl+Y → redo, for the focused tree only. */
export function useUndoShortcuts(undo: (scope: string) => void, redo: (scope: string) => void): void {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod || e.altKey) return;
      const key = e.key.toLowerCase();
      const isUndo = key === "z" && !e.shiftKey;
      const isRedo = (key === "z" && e.shiftKey) || key === "y";
      if (!isUndo && !isRedo) return;
      const scope = undoScopeFor(document.activeElement);
      if (!scope) return;
      // The focused tree owns the key: nothing behind it (a workflow canvas) also undoes.
      e.preventDefault();
      e.stopPropagation();
      if (isUndo) undo(scope);
      else redo(scope);
    };
    // Capture: runs before the panels' own key handlers.
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [undo, redo]);
}
