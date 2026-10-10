import { useEffect, useState } from "react";
import { setVisibleInterval } from "../utils/visibleInterval";
import { getApi } from "../hooks/usePanelApi";

export type TerminalRunner = "mcp" | "user";

export interface TerminalBusyStatus {
  running: boolean;
  runner: TerminalRunner | null;
}

const POLL_MS = 2000;
/** A parked terminal (tab closed, shell alive) is only a dot in the header list: every 10 s. */
const PARKED_EVERY_TICKS = 5;

function deriveRunner(busy?: boolean, running?: boolean): TerminalRunner | null {
  if (!running) return null;
  return busy ? "mcp" : "user";
}

/**
 * One poller for every component that shows terminal activity (the header list and each
 * editor group's tab strip). Each used to poll on its own, every session one bridge call
 * and one walk of every process on the PC, so an open terminal was asked twice every 2 s.
 */
interface Watcher {
  ids: string[];
  parked: ReadonlySet<string>;
  notify: () => void;
}

const watchers = new Set<Watcher>();
const statuses = new Map<string, TerminalBusyStatus>();
const NOT_RUNNING: TerminalBusyStatus = { running: false, runner: null };
let stopPolling: (() => void) | null = null;
let tick = 0;
let inFlight = false;
/** Which sessions a poll asks about: only new ones, the open tabs, or the parked ones too. */
const DUE_NEW = 0;
const DUE_OPEN = 1;
const DUE_ALL = 2;
type Due = typeof DUE_NEW | typeof DUE_OPEN | typeof DUE_ALL;
let pollAgain: Due | null = null;

async function poll(scope: Due): Promise<void> {
  if (inFlight) {
    pollAgain = Math.max(pollAgain ?? scope, scope) as Due;
    return;
  }
  const open = new Set<string>();
  const parked = new Set<string>();
  for (const watcher of watchers) {
    for (const id of watcher.ids) (watcher.parked.has(id) ? parked : open).add(id);
  }
  // A session never asked about yet is always due, parked or not.
  const due = [...new Set([...open, ...parked])].filter((id) =>
    !statuses.has(id) || scope === DUE_ALL || (scope === DUE_OPEN && open.has(id)));
  const api = getApi();
  if (!api || due.length === 0) return;
  inFlight = true;
  let states: Record<string, { ok: boolean; busy?: boolean; running?: boolean }> = {};
  try {
    const result = await api.terminal_busy_many(due);
    states = result?.states ?? {};
  } catch {
    // Same as a session the backend no longer knows: shown as not running.
  } finally {
    inFlight = false;
  }
  const watched = new Set([...watchers].flatMap((watcher) => watcher.ids));
  let changed = false;
  for (const id of due) {
    if (!watched.has(id)) continue; // its tab closed while the poll was out
    const state = states[id];
    const running = !!state?.ok && !!state.running;
    const runner = running ? deriveRunner(state?.busy, running) : null;
    const prev = statuses.get(id);
    if (prev && prev.running === running && prev.runner === runner) continue;
    statuses.set(id, running ? { running, runner } : NOT_RUNNING);
    changed = true;
  }
  if (changed) for (const watcher of watchers) watcher.notify();
  if (pollAgain !== null) {
    const again = pollAgain;
    pollAgain = null;
    void poll(again);
  }
}

function watch(watcher: Watcher): () => void {
  watchers.add(watcher);
  void poll(DUE_NEW);
  if (!stopPolling) {
    tick = 0;
    stopPolling = setVisibleInterval(() => {
      tick = (tick + 1) % PARKED_EVERY_TICKS;
      void poll(tick === 0 ? DUE_ALL : DUE_OPEN);
    }, POLL_MS);
  }
  return () => {
    watchers.delete(watcher);
    const watched = new Set([...watchers].flatMap((w) => w.ids));
    for (const id of [...statuses.keys()]) if (!watched.has(id)) statuses.delete(id);
    if (watchers.size === 0 && stopPolling) {
      stopPolling();
      stopPolling = null;
    }
  };
}

function pick(ids: readonly string[]): Map<string, TerminalBusyStatus> {
  const next = new Map<string, TerminalBusyStatus>();
  for (const id of ids) {
    const status = statuses.get(id);
    if (status) next.set(id, status);
  }
  return next;
}

function sameStatuses(prev: Map<string, TerminalBusyStatus>, next: Map<string, TerminalBusyStatus>): boolean {
  if (prev.size !== next.size) return false;
  for (const [id, status] of next) if (prev.get(id) !== status) return false;
  return true;
}

/** Busy state per session. `parkedSessionIds` (closed tabs whose shell lives on) are asked less often. */
export function useTerminalBusyStatuses(
  sessionIds: string[],
  parkedSessionIds: readonly string[] = [],
): Map<string, TerminalBusyStatus> {
  const [statusMap, setStatusMap] = useState<Map<string, TerminalBusyStatus>>(() => new Map());
  const idsKey = sessionIds.filter(Boolean).sort().join("\0");
  const parkedKey = parkedSessionIds.filter(Boolean).sort().join("\0");

  useEffect(() => {
    const ids = idsKey ? idsKey.split("\0") : [];
    // Only a value that changed re-renders: an idle poll used to re-render the tab strip
    // and, through the header actions, the whole header every 2 s.
    const update = () => setStatusMap((prev) => {
      const next = pick(ids);
      return sameStatuses(prev, next) ? prev : next;
    });
    update();
    if (ids.length === 0) return;
    return watch({ ids, parked: new Set(parkedKey ? parkedKey.split("\0") : []), notify: update });
  }, [idsKey, parkedKey]);

  return statusMap;
}
