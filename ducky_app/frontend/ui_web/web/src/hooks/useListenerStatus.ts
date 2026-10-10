import { useEffect, useSyncExternalStore } from "react";
import { setVisibleInterval } from "../utils/visibleInterval";
import { getApi } from "../hooks/usePanelApi";
import { onApiReady } from "./onApiReady";
import type { ListenerStatus, PanelApi } from "../types/panel";

const OFFLINE: ListenerStatus = { online: false, version: "…" };

type StatusKey = "online" | "wedged" | "offline";

function statusKey(status: ListenerStatus): StatusKey {
  if (status.wedged) return "wedged";
  if (status.online) return "online";
  return "offline";
}

/** Require consecutive bad polls before leaving online or switching wedged/offline. */
function settleListenerStatus(prev: ListenerStatus, next: ListenerStatus, streak: { key: StatusKey; count: number }) {
  const nextKey = statusKey(next);
  if (nextKey === "online") {
    streak.key = "online";
    streak.count = 0;
    return next;
  }

  if (nextKey === streak.key) {
    streak.count += 1;
  } else {
    streak.key = nextKey;
    streak.count = 1;
  }

  if (statusKey(prev) === "online" && streak.count < 2) {
    return prev;
  }
  if (statusKey(prev) !== "online" && nextKey !== statusKey(prev) && streak.count < 2) {
    return prev;
  }

  return next;
}

/**
 * One poll for the whole window, however many components ask for the status.
 *
 * Three call sites used this hook — App, VerseWorkflowBridge, FocusView — each
 * with its own 8-second timer hitting the same endpoint, two of them in the
 * same window. The state is module-level now, so additional consumers are
 * free, and the settle logic sees one consistent stream instead of three
 * racing copies that could disagree about whether the listener is up.
 */
let _status: ListenerStatus = OFFLINE;
const _streak: { key: StatusKey; count: number } = { key: "offline", count: 0 };
let _api: PanelApi | null = null;
let _inFlight = false;
let _started = false;
const _listeners = new Set<() => void>();
let _uptimeSec: number | undefined;
const _uptimeListeners = new Set<() => void>();

/** Everything but the uptime counter, which moves on every poll while UEFN is connected. */
function statusWithoutUptime(status: ListenerStatus): string {
  const { uptime_sec: _uptime, ...rest } = status;
  return JSON.stringify(rest);
}

function _emit(next: ListenerStatus) {
  const settled = settleListenerStatus(_status, next, _streak);
  if (settled.uptime_sec !== _uptimeSec) {
    _uptimeSec = settled.uptime_sec;
    _uptimeListeners.forEach((fn) => fn());
  }
  // Every poll deserializes a new object. Handing it out when nothing changed re-rendered
  // the whole app (every subscriber, App included) every 8 s.
  if (settled === _status || statusWithoutUptime(settled) === statusWithoutUptime(_status)) return;
  _status = settled;
  _listeners.forEach((fn) => fn());
}

function _poll() {
  const api = _api ?? getApi();
  if (!api || typeof api.get_listener_status !== "function" || _inFlight) return;
  _inFlight = true;
  void api
    .get_listener_status()
    .then((next) => _emit(next))
    .catch(() => {
      void api.get_version().then((version) => _emit({ online: false, version }));
    })
    .finally(() => {
      _inFlight = false;
    });
}

function _ensureStarted(pollMs: number) {
  if (_started) return;
  _started = true;
  onApiReady((api) => {
    _api = api;
    _poll();
    setVisibleInterval(_poll, pollMs);
  });
}

function _subscribe(listener: () => void) {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

function _snapshot(): ListenerStatus {
  return _status;
}

function _subscribeUptime(listener: () => void) {
  _uptimeListeners.add(listener);
  return () => {
    _uptimeListeners.delete(listener);
  };
}

function _uptimeSnapshot(): number | undefined {
  return _uptimeSec;
}

const _noSubscribe = () => () => {};

/**
 * The listener's latest uptime; the status object keeps the value from its last real
 * change. With `live` false it reads the latest value on render but does not re-render
 * on every poll (a closed menu has nothing to show).
 */
export function useListenerUptimeSec(live = true): number | undefined {
  return useSyncExternalStore(live ? _subscribeUptime : _noSubscribe, _uptimeSnapshot, _uptimeSnapshot);
}

/** Refresh outside the poll cadence (e.g. straight after a deploy). */
export function refreshListenerStatus(): void {
  _poll();
}

export function useListenerStatus(pollMs = 8000, refreshToken = 0) {
  const status = useSyncExternalStore(_subscribe, _snapshot, _snapshot);

  useEffect(() => {
    _ensureStarted(pollMs);
  }, [pollMs]);

  useEffect(() => {
    if (refreshToken === 0) return;
    _poll();
  }, [refreshToken]);

  return status;
}
