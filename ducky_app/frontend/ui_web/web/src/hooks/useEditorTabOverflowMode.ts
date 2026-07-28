import { useCallback, useEffect, useState } from "react";

/** Chrome-style shrink-to-fit (default) vs fixed-width tabs with a scrollbar. */
export type EditorTabOverflowMode = "chrome" | "scrollbar";

const STORAGE_KEY = "uefn-editor-tab-overflow-mode";
const CHANGE_EVENT = "uefn-editor-tab-overflow-mode";

function readMode(): EditorTabOverflowMode {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "scrollbar" || raw === "chrome") return raw;
  } catch {
    // ignore storage errors
  }
  return "chrome";
}

export function useEditorTabOverflowMode() {
  const [mode, setModeState] = useState<EditorTabOverflowMode>(readMode);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setModeState(readMode());
    };
    const onCustom = () => setModeState(readMode());
    window.addEventListener("storage", onStorage);
    window.addEventListener(CHANGE_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(CHANGE_EVENT, onCustom);
    };
  }, []);

  const setMode = useCallback((next: EditorTabOverflowMode) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore storage errors
    }
    setModeState(next);
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  }, []);

  return { mode, setMode };
}
