import { useSyncExternalStore } from "react";
import { onApiReady } from "../hooks/onApiReady";
import { getApi } from "../hooks/usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import type { TeamOverview } from "../types/panel";

/**
 * The team this PC works in, the same on every tab (a chat, a plugin, Settings): the team copy
 * most plugins show and every plugin on it (`plugin_team_overview`). The title bar's Team button
 * and the Team modal read this, never the plugin pane on screen. Read once the API is ready, then
 * again on any scope or live push (no timer).
 */

export type ActiveTeam = TeamOverview & {
  /** A team copy (false: everything is Local). */
  shared: boolean;
  incoming: number;
  pending: number;
  conflicts: number;
  blocked: number;
  failing: boolean;
  /** To send + coming in + conflicts + failed downloads. */
  badge: number;
  phrase: string;
  tone: "" | "warn" | "error";
};

/** Pushes that land together (a switch moves every plugin) make one read. */
const SETTLE_MS = 300;

let team: ActiveTeam | null = null;
const listeners = new Set<() => void>();
let started = false;
let stopPush: (() => void) | null = null;
let stopReady: (() => void) | null = null;
let settle: number | undefined;

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function summarizeTeam(o: TeamOverview): ActiveTeam {
  const plugins = o.plugins ?? [];
  const sum = (pick: (p: TeamOverview["plugins"][number]) => number) => plugins.reduce((n, p) => n + pick(p), 0);
  const incoming = sum((p) => p.incoming);
  const pending = sum((p) => p.pending || p.outgoing.length);
  const conflicts = sum((p) => p.conflicts);
  const blocked = sum((p) => p.blocked.length);
  const failing = plugins.some((p) => p.state === "offline" || p.state === "error");
  const shared = o.current !== "personal";
  const phrase = !shared ? "Not on a team"
    : conflicts ? plural(conflicts, "conflict")
      : failing ? "Couldn't sync"
        : [incoming ? `${incoming} coming in` : "", pending ? `${pending} to send` : ""].filter(Boolean).join(" · ") || "Up to date";
  return {
    ...o, plugins, shared, incoming, pending, conflicts, blocked, failing,
    badge: shared ? incoming + pending + conflicts + blocked : 0,
    phrase,
    tone: conflicts || failing ? "error" : blocked ? "warn" : "",
  };
}

function tell(): void {
  for (const fn of [...listeners]) fn();
}

export async function readActiveTeam(): Promise<void> {
  const api = getApi();
  if (!api?.plugin_team_overview) {
    // An app without the call (the phone on an older PC): everything reads as Local.
    if (!team) {
      team = summarizeTeam({ current: "personal", label: "Local", plugins: [] });
      tell();
    }
    return;
  }
  try {
    const got = await api.plugin_team_overview();
    if (!got || got.ok === false) return;
    team = summarizeTeam(got);
    tell();
  } catch {
    /* keep the last read */
  }
}

export function refreshActiveTeam(): void {
  window.clearTimeout(settle);
  settle = window.setTimeout(() => void readActiveTeam(), SETTLE_MS);
}

function start(): void {
  if (started) return;
  started = true;
  installPanelPushBus();
  stopPush = subscribePanelPush((event) => {
    if (event.type === "plugin_scope_changed" || event.type === "live_team" || event.type === "duckyos_account_changed") {
      refreshActiveTeam();
    }
  });
  stopReady = onApiReady(() => void readActiveTeam());
}

function subscribe(fn: () => void): () => void {
  start();
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

const snapshot = () => team;

export function useActiveTeam(): ActiveTeam | null {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function _resetActiveTeamForTests(): void {
  stopPush?.();
  stopPush = null;
  stopReady?.();
  stopReady = null;
  started = false;
  team = null;
  listeners.clear();
  window.clearTimeout(settle);
}
