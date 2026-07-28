import { useSyncExternalStore } from "react";
import type { AgentEvent } from "../types/panel";
import { getApi } from "./usePanelApi";
import { agentEventRunningSignal } from "./chatRun/runningSignal";
import { syncChatTurnTimersFromRunningSet } from "./chatTurnTimer";
import { subscribeAgentEvents } from "./useAgentEventBus";

/** Module-level running set so every consumer shares one poll + one event subscription. */
let _runningIds = new Set<string>();
const _listeners = new Set<() => void>();
let _started = false;

function _emit() {
  for (const l of _listeners) l();
}

function _setRunning(next: Set<string>) {
  if (next.size === _runningIds.size) {
    let same = true;
    for (const id of next) {
      if (!_runningIds.has(id)) {
        same = false;
        break;
      }
    }
    if (same) return;
  }
  const prev = _runningIds;
  _runningIds = next;
  syncChatTurnTimersFromRunningSet(prev, next);
  _emit();
}

function _applyEvent(event: AgentEvent) {
  const convId = event.conv_id;
  if (!convId) return;
  const signal = agentEventRunningSignal(event);
  if (!signal) return;
  const has = _runningIds.has(convId);
  if (signal === "start") {
    if (has) return;
    const next = new Set(_runningIds);
    next.add(convId);
    _setRunning(next);
    return;
  }
  if (!has) return;
  const next = new Set(_runningIds);
  next.delete(convId);
  _setRunning(next);
}

async function _syncFromApi() {
  const api = getApi();
  if (!api?.list_running_agents) return;
  const ids = await api.list_running_agents();
  _setRunning(new Set(ids));
}

function _ensureStarted(pollMs: number) {
  if (_started) return;
  _started = true;
  subscribeAgentEvents(_applyEvent);
  void _syncFromApi();
  window.setInterval(() => void _syncFromApi(), pollMs);
}

function subscribe(listener: () => void) {
  _listeners.add(listener);
  _ensureStarted(10000);
  return () => {
    _listeners.delete(listener);
  };
}

function getSnapshot() {
  return _runningIds;
}

/** Tracks conversation IDs with an active agent thread (shared store + slow poll). */
export function useRunningAgents(_pollMs = 10000): Set<string> {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Imperative read for non-React helpers (linked-agent status sync). */
export function getRunningAgentIds(): Set<string> {
  return _runningIds;
}
