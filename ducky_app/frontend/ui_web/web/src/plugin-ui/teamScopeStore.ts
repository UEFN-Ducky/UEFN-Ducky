import { useSyncExternalStore } from "react";
import type { PluginScopeChoice, PluginScopeStatus } from "../types/panel";
import type { StatusPhrase } from "./scopeBarText";

/** A button on a note ("Upload my copy", "Dismiss"). */
export type TeamScopeNoteAction = { label: string; run: () => void; disabled?: boolean; ghost?: boolean };
/** A line that needs a decision or explains a refusal (reset, paused, refused write, error, storage). */
export type TeamScopeNote = { key: string; text: string; tone?: "warn" | "error"; actions?: TeamScopeNoteAction[] };

/** The visible pane's scope bar callbacks: the Team modal runs these, never its own copy. */
export type TeamScopeActions = {
  /** Update now / Get: bring everyone's changes in now. */
  pull: () => Promise<boolean>;
  /** Send this PC's changes now (Manual). */
  push: () => Promise<boolean>;
  /** A full round now (Retry). */
  syncNow: () => Promise<void>;
  /** Switch whose data the plugin shows (asks first). */
  choose: (choice: PluginScopeChoice) => Promise<void>;
  /** Copy Local into a team. */
  copyInto: (offer: { space: string; label: string }) => Promise<void>;
  /** After a server reset: upload or clear this PC's copy (asks first). */
  settle: (action: "upload" | "clear") => Promise<void>;
  /** Read the teams to pick from into `menu`. */
  loadChoices: () => Promise<void>;
  /** Join a teammate's live group; absent when live can't be joined from here. */
  joinLive?: (room?: string) => Promise<void>;
  /** Re-read the status after an action (`dataChanged`: the plugin's data changed). */
  refresh: (dataChanged?: boolean) => Promise<void>;
};

/** What the header's Team button and modal show: the visible plugin pane's team copy. */
export type TeamScope = {
  pluginId: string;
  /** The plugin's name. */
  plugin: string;
  status: PluginScopeStatus;
  phrase: StatusPhrase;
  syncing: boolean;
  busy: boolean;
  /** A switch to a team is running: the picker waits. */
  switching: boolean;
  shared: boolean;
  /** Dot colour: shared, paused (read-only) or personal (Local). */
  variant: "shared" | "paused" | "personal";
  /** The provider's kind of space, lower case ("team"). */
  kind: string;
  /** The picked choice id: a space id, or "personal". */
  current: string;
  /** Teams to pick from; null until {@link TeamScopeActions.loadChoices} answers. */
  menu: PluginScopeChoice[] | null;
  /** Teams to copy Local into. */
  offers: { space: string; label: string }[];
  /** Why a folder can't be shared from this PC ("" when it can). */
  refusal: string;
  manual: boolean;
  canSync: boolean;
  failing: boolean;
  canPush: boolean;
  canPull: boolean;
  /** To send + coming in + conflicts + not shared (the old Source control count). */
  badge: number;
  conflicts: number;
  notes: TeamScopeNote[];
  actions: TeamScopeActions;
};

let current: TeamScope | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const fn of listeners) fn();
}

/** The visible pane says what it shows (only the pane whose `active` is true calls this). */
export function publishTeamScope(scope: TeamScope): void {
  current = scope;
  emit();
}

/** The pane went hidden or stopped showing a team bar: clear it if it was the one shown. */
export function unpublishTeamScope(pluginId: string): void {
  if (current?.pluginId !== pluginId) return;
  current = null;
  emit();
}

export function getTeamScope(): TeamScope | null {
  return current;
}

export function subscribeTeamScope(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useTeamScope(): TeamScope | null {
  return useSyncExternalStore(subscribeTeamScope, getTeamScope, getTeamScope);
}

// Every pane, shown or hidden (a round in a hidden tab still gets its tray row). A hidden pane
// reads no status, so its entry is what it last read.
let all: Record<string, TeamScope> = {};
const allListeners = new Set<() => void>();

function emitAll() {
  for (const fn of allListeners) fn();
}

/** A pane (active or not) says what it shows. */
export function publishPaneScope(scope: TeamScope): void {
  all = { ...all, [scope.pluginId]: scope };
  emitAll();
}

/** The pane closed or stopped showing a team bar. */
export function unpublishPaneScope(pluginId: string): void {
  if (!(pluginId in all)) return;
  const { [pluginId]: _gone, ...rest } = all;
  all = rest;
  emitAll();
}

/** Every pane's copy by plugin id (a new object on each change). */
export function getAllTeamScopes(): Record<string, TeamScope> {
  return all;
}

export function subscribeAllTeamScopes(fn: () => void): () => void {
  allListeners.add(fn);
  return () => {
    allListeners.delete(fn);
  };
}
