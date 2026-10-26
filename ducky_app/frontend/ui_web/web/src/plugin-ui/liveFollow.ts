import { useSyncExternalStore } from "react";

/**
 * "Same screen as the group" in the title bar's Live menu: one setting for every plugin, per
 * viewer, on until turned off. In a teammate's group each plugin's page goes to the leader's
 * screen while it is on. A convenience: with storage off it stays on for this session.
 */
const KEY = "ducky.liveFollow";
const listeners = new Set<() => void>();

export function readLiveFollow(): boolean {
  try {
    return localStorage.getItem(KEY) !== "0";
  } catch {
    return true;
  }
}

export function writeLiveFollow(on: boolean): void {
  try {
    if (on) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, "0");
  } catch {
    /* storage off: following stays as it is for this session */
  }
  for (const fn of [...listeners]) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  const other = (event: StorageEvent) => {
    if (event.key === KEY) fn(); // another window changed it
  };
  window.addEventListener("storage", other);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", other);
  };
}

export function useLiveFollow(): boolean {
  return useSyncExternalStore(subscribe, readLiveFollow, readLiveFollow);
}
