// Open/focus a Discord Ducky editor tab (one per bot). The dock panel's "Open as
// tab" button and the header dropdown call requestOpenDiscordTab(botId?).
// ChatView registers the opener.
//
// Also tracks whether ANY Discord tab is occupied in THIS app session: while a
// Discord tab is open in the main window OR any focus window, the dock rails
// hide the Discord Ducky panel (one Discord chrome at a time) and restore it
// where it was the moment Discord is closed everywhere.

import { useSyncExternalStore } from "react";

export type OpenDiscordTabFn = (botId?: string, label?: string) => void;

let opener: OpenDiscordTabFn | null = null;
let lastRequestAt = 0;
let lastRequestKey = "";

export function registerOpenDiscordTab(fn: OpenDiscordTabFn): () => void {
  opener = fn;
  return () => {
    if (opener === fn) opener = null;
  };
}

export function requestOpenDiscordTab(botId?: string, label?: string): void {
  const key = `${botId || "default"}|${label || ""}`;
  const now = Date.now();
  if (key === lastRequestKey && now - lastRequestAt < 400) return;
  lastRequestAt = now;
  lastRequestKey = key;
  opener?.(botId, label);
}

let discordTabOpen = false;
const tabOpenListeners = new Set<() => void>();

/** ChatView publishes this from its openTabs — the single source of tab truth. */
export function setDiscordTabOpen(open: boolean): void {
  if (open === discordTabOpen) return;
  discordTabOpen = open;
  for (const fn of [...tabOpenListeners]) fn();
}

function subscribeDiscordTabOpen(fn: () => void): () => void {
  tabOpenListeners.add(fn);
  return () => {
    tabOpenListeners.delete(fn);
  };
}

function getDiscordTabOpen(): boolean {
  return discordTabOpen;
}

/** True while any Discord Ducky tab is open in the main editor or a focus window. */
export function useDiscordTabOpen(): boolean {
  return useSyncExternalStore(subscribeDiscordTabOpen, getDiscordTabOpen);
}
