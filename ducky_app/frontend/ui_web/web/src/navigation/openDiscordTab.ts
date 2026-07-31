// Open/focus the Discord plugin chat panel (Phase-2 HTML). Dock "Open as tab"
// and legacy callers use requestOpenDiscordTab().
//
// Also tracks whether ANY Discord chat tab is occupied in THIS app session:
// while open, dock rails hide the Discord Ducky panel (one chrome at a time).

import { useSyncExternalStore } from "react";
import { requestOpenPluginUiTab } from "../plugin-ui/openPluginUiTab";

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

export function requestOpenDiscordTab(_botId?: string, _label?: string): void {
  const key = "plugin:discord:discord-chat";
  const now = Date.now();
  if (key === lastRequestKey && now - lastRequestAt < 400) return;
  lastRequestAt = now;
  lastRequestKey = key;
  if (opener) {
    opener(_botId, _label);
    return;
  }
  requestOpenPluginUiTab("discord", "discord-chat");
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
