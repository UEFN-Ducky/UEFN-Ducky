import { createContext, useContext, useMemo, useRef, type ReactNode } from "react";
import {
  createScopedUndoHistory,
  type ScopedUndoHistory,
  type UndoableAction,
  type UndoScope,
} from "./scopedUndoHistory";

export type { UndoableAction, UndoScope } from "./scopedUndoHistory";

export interface UndoHistoryValue {
  /** Record an action in one tree's history (Duckies, Content or Workflows). */
  push: (scope: UndoScope, action: UndoableAction) => void;
  undo: (scope: string) => void;
  redo: (scope: string) => void;
  canUndo: (scope: string) => boolean;
  canRedo: (scope: string) => boolean;
}

const UndoHistoryContext = createContext<UndoHistoryValue | null>(null);

/** One in-memory history per tree for the whole window; it lasts until the app closes. */
export function UndoHistoryProvider({ children }: { children: ReactNode }) {
  const historyRef = useRef<ScopedUndoHistory | null>(null);
  if (!historyRef.current) historyRef.current = createScopedUndoHistory();
  const history = historyRef.current;

  const value = useMemo<UndoHistoryValue>(
    () => ({
      push: history.push,
      undo: (scope) => void history.undo(scope),
      redo: (scope) => void history.redo(scope),
      canUndo: history.canUndo,
      canRedo: history.canRedo,
    }),
    [history],
  );

  return <UndoHistoryContext.Provider value={value}>{children}</UndoHistoryContext.Provider>;
}

/** Non-throwing — returns null outside a provider (e.g. focus windows). */
export function useUndoHistoryOptional(): UndoHistoryValue | null {
  return useContext(UndoHistoryContext);
}
