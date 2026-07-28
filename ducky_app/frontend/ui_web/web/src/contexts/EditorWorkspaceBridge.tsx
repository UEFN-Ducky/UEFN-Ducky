import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

type FlushFn = () => Promise<void>;

interface EditorWorkspaceBridgeValue {
  registerFlush: (fn: FlushFn | null) => void;
  flushBeforeSwitch: () => Promise<void>;
}

const EditorWorkspaceBridgeContext = createContext<EditorWorkspaceBridgeValue | null>(null);

export function EditorWorkspaceBridgeProvider({ children }: { children: ReactNode }) {
  const flushRef = useRef<FlushFn | null>(null);

  const registerFlush = useCallback((fn: FlushFn | null) => {
    flushRef.current = fn;
  }, []);

  const flushBeforeSwitch = useCallback(async () => {
    await flushRef.current?.();
  }, []);

  const value = useMemo(
    () => ({ registerFlush, flushBeforeSwitch }),
    [registerFlush, flushBeforeSwitch],
  );

  return (
    <EditorWorkspaceBridgeContext.Provider value={value}>{children}</EditorWorkspaceBridgeContext.Provider>
  );
}

export function useEditorWorkspaceBridge(): EditorWorkspaceBridgeValue {
  const ctx = useContext(EditorWorkspaceBridgeContext);
  if (!ctx) {
    throw new Error("useEditorWorkspaceBridge must be used within EditorWorkspaceBridgeProvider");
  }
  return ctx;
}

export function useRegisterEditorWorkspaceFlush(fn: FlushFn) {
  const { registerFlush } = useEditorWorkspaceBridge();
  useEffect(() => {
    registerFlush(fn);
    return () => registerFlush(null);
  }, [fn, registerFlush]);
}

export function useOptionalEditorWorkspaceFlush(): (() => Promise<void>) | undefined {
  const ctx = useContext(EditorWorkspaceBridgeContext);
  return ctx?.flushBeforeSwitch;
}
