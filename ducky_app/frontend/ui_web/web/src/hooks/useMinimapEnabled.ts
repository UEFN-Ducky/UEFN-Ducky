import { useCallback, useEffect, useState } from "react";
import { normPath } from "../verse-editor/utils/isVerseFile";

const STORAGE_PREFIX = "uefn-verse-minimap-enabled:";
const CHANGE_EVENT = "uefn-verse-minimap-enabled";

function storageKey(filePath: string): string {
  return `${STORAGE_PREFIX}${normPath(filePath)}`;
}

function readEnabled(filePath: string): boolean {
  try {
    const raw = localStorage.getItem(storageKey(filePath));
    if (raw === "false") return false;
    if (raw === "true") return true;
  } catch {
    // ignore storage errors
  }
  return true;
}

/** Per-file Verse editor minimap visibility (Monaco `minimap.enabled`). */
export function useMinimapEnabled(filePath: string) {
  const key = normPath(filePath);
  const [enabled, setEnabledState] = useState(() => readEnabled(key));

  useEffect(() => {
    setEnabledState(readEnabled(key));
  }, [key]);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === storageKey(key)) setEnabledState(readEnabled(key));
    };
    const onCustom = (e: Event) => {
      const detail = (e as CustomEvent<{ path?: string }>).detail;
      if (!detail?.path || normPath(detail.path) === key) {
        setEnabledState(readEnabled(key));
      }
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(CHANGE_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(CHANGE_EVENT, onCustom);
    };
  }, [key]);

  const setEnabled = useCallback(
    (next: boolean) => {
      try {
        localStorage.setItem(storageKey(key), String(next));
      } catch {
        // ignore storage errors
      }
      setEnabledState(next);
      window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { path: key } }));
    },
    [key],
  );

  const toggle = useCallback(() => {
    setEnabled(!readEnabled(key));
  }, [key, setEnabled]);

  return { enabled, setEnabled, toggle };
}
