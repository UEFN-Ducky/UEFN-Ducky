import { useSyncExternalStore } from "react";
import { getApi } from "./usePanelApi";

let _hasApiKey = false;
const _listeners = new Set<() => void>();
let _started = false;

function _emit() {
  for (const l of _listeners) l();
}

function _set(value: boolean) {
  if (_hasApiKey === value) return;
  _hasApiKey = value;
  _emit();
}

async function _sync() {
  const api = getApi();
  if (!api?.has_any_api_key) return;
  _set(await api.has_any_api_key());
}

function _ensureStarted() {
  if (_started) return;
  _started = true;
  const refresh = () => void _sync();
  refresh();
  window.addEventListener("pywebviewready", refresh);
  window.setInterval(refresh, 15000);
}

function subscribe(listener: () => void) {
  _listeners.add(listener);
  _ensureStarted();
  return () => {
    _listeners.delete(listener);
  };
}

function getSnapshot() {
  return _hasApiKey;
}

/** Shared app-level API-key presence (one poll for all ChatPanes). */
export function useHasApiKey(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Call after settings save so the composer unlocks without waiting for the poll. */
export function refreshHasApiKey(): void {
  void _sync();
}
