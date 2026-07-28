import { useCallback, useEffect, useState } from "react";
import type { ChatLayoutMode } from "../types/panel";

const STORAGE_KEY = "uefn-chat-layout-mode";
const CHANGE_EVENT = "uefn-chat-layout-mode";

function readMode(): ChatLayoutMode {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "sidebarOnly") return "full";
    if (raw === "full" || raw === "sidebarHidden") return raw;
  } catch {
    // ignore
  }
  return "full";
}

export function useChatLayoutMode() {
  const [mode, setModeState] = useState<ChatLayoutMode>(readMode);

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

  const setMode = useCallback((next: ChatLayoutMode) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore
    }
    setModeState(next);
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  }, []);

  return { mode, setMode };
}
