import { useSyncExternalStore } from "react";
import type { LiveOverview } from "../types/panel";
import { upsertBackgroundJob } from "./backgroundActivity";
import { getApi } from "./usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";

/**
 * The title bar's Live menu: every team copy this PC has plugins on and who is live where
 * (backend/uefn_plugins/live.py `overview`). Read once, then again when the app says it
 * moved (`live_team`, an account change, any plugin scope push): no timer runs while
 * nothing changes.
 */

/** Pushes that land together (a join moves every plugin) make one read. */
const SETTLE_MS = 300;
/** "Connecting to …" turns into linked or "Can't reach" after the host's grace (live.py LINK_GRACE_S). */
const RECHECK_MS = 11_000;

let overview: LiveOverview | null = null;
const listeners = new Set<() => void>();
let started = false;
let stopPush: (() => void) | null = null;
let settle: number | undefined;
let recheck: number | undefined;

function tell(): void {
  for (const fn of [...listeners]) fn();
}

export async function readLiveOverview(): Promise<void> {
  const api = getApi();
  if (!api?.live_overview) return;
  try {
    const got = await api.live_overview();
    if (!got || got.ok === false) return;
    overview = got;
    tell();
    window.clearTimeout(recheck);
    if (got.teams?.some((t) => t.connecting?.length)) recheck = window.setTimeout(() => refreshLiveOverview(), RECHECK_MS);
  } catch {
    /* keep the last menu */
  }
}

export function refreshLiveOverview(): void {
  window.clearTimeout(settle);
  settle = window.setTimeout(() => void readLiveOverview(), SETTLE_MS);
}

/** Live ended by itself on a team copy: a row in the activity list says so. */
function sayEnded(space: string): void {
  const label = overview?.teams?.find((t) => t.space === space)?.label || "the team";
  upsertBackgroundJob({
    id: `live-ended:${space}`,
    source: "plugin-data",
    title: `Live ended in ${label}`,
    detail: "Everyone else left the group. Your changes keep syncing with the team as before.",
    phase: "done",
  });
}

function start(): void {
  if (started) return;
  started = true;
  installPanelPushBus();
  stopPush = subscribePanelPush((event) => {
    if (event.type === "live_team") {
      if (event.ended === "alone" && event.space) sayEnded(event.space);
      refreshLiveOverview();
    } else if (event.type === "duckyos_account_changed" || event.type === "plugin_scope_changed") {
      // Any scope push, not just a switch: the first read at startup can land before the
      // account and its team links are in, and then nothing else would read again.
      refreshLiveOverview();
    }
  });
  void readLiveOverview();
}

function subscribe(fn: () => void): () => void {
  start();
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

const snapshot = () => overview;

export function useLiveOverview(): LiveOverview | null {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function _resetLiveOverviewForTests(): void {
  stopPush?.();
  stopPush = null;
  started = false;
  overview = null;
  listeners.clear();
  window.clearTimeout(settle);
  window.clearTimeout(recheck);
}
