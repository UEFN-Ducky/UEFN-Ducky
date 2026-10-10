/** A reversible action (command pattern). `undo` reverses it; `redo` re-applies it.
 * Closures own any state that changes across cycles (e.g. a trash token that is minted
 * fresh on every re-delete), so undo→redo→undo stays correct. */
export interface UndoableAction {
  label: string;
  undo: () => void | Promise<void>;
  redo: () => void | Promise<void>;
}

/** Each tree keeps its own history: Ctrl+Z in Content never undoes a Duckies move. */
export type UndoScope = "chats" | "files" | "workflows";

const MAX_UNDO = 100;

type Stacks = { undo: UndoableAction[]; redo: UndoableAction[]; busy: boolean };

export interface ScopedUndoHistory {
  push: (scope: UndoScope, action: UndoableAction) => void;
  /** Resolves true when an action ran. */
  undo: (scope: string) => Promise<boolean>;
  redo: (scope: string) => Promise<boolean>;
  canUndo: (scope: string) => boolean;
  canRedo: (scope: string) => boolean;
}

/**
 * Undo/redo stacks per tree, kept in memory only: they last while the app runs (tabs
 * and panels can switch) and are gone when it closes. Nothing is written anywhere.
 */
export function createScopedUndoHistory(max = MAX_UNDO): ScopedUndoHistory {
  const stacks = new Map<string, Stacks>();
  const stacksFor = (scope: string): Stacks => {
    let found = stacks.get(scope);
    if (!found) {
      found = { undo: [], redo: [], busy: false };
      stacks.set(scope, found);
    }
    return found;
  };

  // Run one direction; on success move the action to the other stack, on failure put it
  // back so it can be retried. `busy` serializes rapid Ctrl+Z presses per tree.
  const run = async (scope: string, direction: "undo" | "redo"): Promise<boolean> => {
    const s = stacksFor(scope);
    if (s.busy) return false;
    const from = direction === "undo" ? s.undo : s.redo;
    const to = direction === "undo" ? s.redo : s.undo;
    const action = from.pop();
    if (!action) return false;
    s.busy = true;
    try {
      await (direction === "undo" ? action.undo() : action.redo());
      to.push(action);
      return true;
    } catch {
      from.push(action);
      return false;
    } finally {
      s.busy = false;
    }
  };

  return {
    push: (scope, action) => {
      const s = stacksFor(scope);
      s.undo.push(action);
      if (s.undo.length > max) s.undo.shift();
      s.redo = []; // a fresh action invalidates this tree's redo branch
    },
    undo: (scope) => run(scope, "undo"),
    redo: (scope) => run(scope, "redo"),
    canUndo: (scope) => (stacks.get(scope)?.undo.length ?? 0) > 0,
    canRedo: (scope) => (stacks.get(scope)?.redo.length ?? 0) > 0,
  };
}
