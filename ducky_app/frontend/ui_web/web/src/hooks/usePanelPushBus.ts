import { useEffect } from "react";
import type { PanelPushEvent } from "../types/panel";

type PanelPushListener = (event: PanelPushEvent) => void;

const listeners = new Set<PanelPushListener>();

function fanOut(event: PanelPushEvent) {
  for (const listener of listeners) {
    try {
      listener(event);
    } catch {
      // ignore subscriber errors
    }
  }
}

let busInstalled = false;

export function installPanelPushBus() {
  if (busInstalled) return;
  busInstalled = true;
  // Plugin shell.boot may wrap __uefnPanelPush before React mounts — keep it.
  const existing = window.__uefnPanelPush;
  window.__uefnPanelPush = (event) => {
    fanOut(event);
    if (typeof existing === "function" && existing !== fanOut) {
      try {
        existing(event);
      } catch {
        // ignore wrapper errors
      }
    }
  };
}

export function subscribePanelPush(listener: PanelPushListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePanelPushBus() {
  useEffect(() => {
    installPanelPushBus();
  }, []);
}
