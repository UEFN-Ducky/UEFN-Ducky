import type { AgentEvent, PanelPushEvent } from "../types/panel";
import { pushLocalAgentEvent } from "./useAgentEventBus";
import { installPanelPushBus } from "./usePanelPushBus";

/**
 * The native launcher answers the page's first getters from its start-up cache, then asks
 * Python again. When Python's answer differs it fires this event with `{stale: [names]}`.
 * Each stale getter is mapped to the update the page already listens for, so the loaders
 * that made those calls run again in place: no page reload, no lost scroll or typing.
 */
export const STARTUP_REFRESH_EVENT = "ducky-native-startup-refresh";

/** Getter -> panel push type whose listeners re-read it. */
const PANEL_PUSH: Record<string, string> = {
  get_appearance: "appearance_changed",
  get_models: "models_updated",
  get_models_catalog: "models_updated",
  get_key_status: "models_updated",
  has_any_api_key: "models_updated",
  list_uefn_plugins: "uefn_plugins_changed",
  list_workflows: "graphs_changed",
};

/** Getter -> agent event type whose listeners re-read it. */
const AGENT_EVENT: Record<string, string> = {
  get_settings: "settings_changed",
  list_conversations: "chats_changed",
  list_all_conversations: "chats_changed",
  list_folders: "chats_changed",
  list_agent_profiles: "duckies_changed",
  list_ducky_catalog: "duckies_changed",
};

/** Push the existing update events for these stale getters (each event once). */
export function refreshStale(stale: readonly string[]): void {
  const panel = new Set<string>();
  const agent = new Set<string>();
  for (const name of stale) {
    if (PANEL_PUSH[name]) panel.add(PANEL_PUSH[name]);
    else if (AGENT_EVENT[name]) agent.add(AGENT_EVENT[name]);
    // ponytail: getters with no update path (header visibility, dock, restore tabs, …)
    // keep the cached value until the next start; add an event when one goes stale in practice.
  }
  installPanelPushBus();
  for (const type of panel) window.__uefnPanelPush?.({ type } as PanelPushEvent);
  for (const type of agent) pushLocalAgentEvent({ type } as AgentEvent);
}

let installed = false;

export function installStartupRefresh(): void {
  if (installed) return;
  installed = true;
  window.addEventListener(STARTUP_REFRESH_EVENT, (ev) => {
    const stale = (ev as CustomEvent<{ stale?: unknown }>).detail?.stale;
    if (Array.isArray(stale)) refreshStale(stale.filter((s): s is string => typeof s === "string"));
  });
}
