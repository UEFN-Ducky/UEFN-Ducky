import { useSyncExternalStore } from "react";
import { setVisibleInterval } from "../utils/visibleInterval";
import { getApi } from "./usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";

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
  try {
    const api = getApi();
    if (!api?.has_any_api_key) return;
    _set(await api.has_any_api_key());
  } catch {
    /* tunnel blip — next interval retries */
  }
}

function _ensureStarted() {
  if (_started) return;
  _started = true;
  const refresh = () => void _sync();
  refresh();
  window.addEventListener("pywebviewready", refresh);
  setVisibleInterval(refresh, 15000);
  installPanelPushBus();
  subscribePanelPush((event) => {
    if (
      event.type === "key_test_done" ||
      event.type === "uefn_plugins_changed" ||
      event.type === "models_updated"
    ) {
      refresh();
    }
  });
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
