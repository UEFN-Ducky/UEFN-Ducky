import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

/** A reversible action (command pattern). `undo` reverses it; `redo` re-applies it.
 * Closures own any state that changes across cycles (e.g. a trash token that is minted
 * fresh on every re-delete), so undo→redo→undo stays correct. */
export interface UndoableAction {
  label: string;
  undo: () => void | Promise<void>;
  redo: () => void | Promise<void>;
}

const MAX_UNDO = 100;

export interface UndoHistoryValue {
  push: (action: UndoableAction) => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

const UndoHistoryContext = createContext<UndoHistoryValue | null>(null);

export function UndoHistoryProvider({ children }: { children: ReactNode }) {
  const undoStackRef = useRef<UndoableAction[]>([]);
  const redoStackRef = useRef<UndoableAction[]>([]);
  const busyRef = useRef(false);
  const [flags, setFlags] = useState({ canUndo: false, canRedo: false });

  const syncFlags = useCallback(() => {
    setFlags((prev) => {
      const canUndo = undoStackRef.current.length > 0;
      const canRedo = redoStackRef.current.length > 0;
      return prev.canUndo === canUndo && prev.canRedo === canRedo
        ? prev
        : { canUndo, canRedo };
    });
  }, []);

  const push = useCallback(
    (action: UndoableAction) => {
      undoStackRef.current.push(action);
      if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
      redoStackRef.current = []; // a fresh action invalidates the redo branch
      syncFlags();
    },
    [syncFlags],
  );

  // Run one direction; on success move the action to the other stack, on failure put it
  // back so it can be retried. `busyRef` serializes rapid Ctrl+Z presses.
  const run = useCallback(
    (from: "undo" | "redo") => {
      if (busyRef.current) return;
      const src = from === "undo" ? undoStackRef.current : redoStackRef.current;
      const dst = from === "undo" ? redoStackRef.current : undoStackRef.current;
      const action = src.pop();
      if (!action) return;
      busyRef.current = true;
      syncFlags();
      Promise.resolve()
        .then(() => (from === "undo" ? action.undo() : action.redo()))
        .then(
          () => {
            dst.push(action);
          },
          () => {
            src.push(action); // failed — leave it where it was
          },
        )
        .finally(() => {
          busyRef.current = false;
          syncFlags();
        });
    },
    [syncFlags],
  );

  const undo = useCallback(() => run("undo"), [run]);
  const redo = useCallback(() => run("redo"), [run]);

  const value = useMemo<UndoHistoryValue>(
    () => ({ push, undo, redo, canUndo: flags.canUndo, canRedo: flags.canRedo }),
    [push, undo, redo, flags.canUndo, flags.canRedo],
  );

  return <UndoHistoryContext.Provider value={value}>{children}</UndoHistoryContext.Provider>;
}

/** Non-throwing — returns null outside a provider (e.g. focus windows). */
export function useUndoHistoryOptional(): UndoHistoryValue | null {
  return useContext(UndoHistoryContext);
}
