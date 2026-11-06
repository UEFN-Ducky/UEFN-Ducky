import type { AgentEvent, PanelPushEvent } from "../types/panel";
import { pushLocalAgentEvent } from "./useAgentEventBus";
import { installPanelPushBus } from "./usePanelPushBus";

/**
 * The native launcher answers the page's first getters from its start-up cache, then asks
 * Python again. When Python's answer differs it fires this event with `{stale: [names]}`.
 * Each stale getter is mapped to the updates the page already listens for, so the loaders
 * that made those calls run again in place: no page reload, no lost scroll or typing.
 *
 * The launcher only caches getters listed here (native_launcher/crates/shim/src/cache.rs
 * CACHEABLE; a cargo test checks every one has an entry). A getter whose start-up callers
 * don't all re-read on one of these events must not be added to either list.
 */
export const STARTUP_REFRESH_EVENT = "ducky-native-startup-refresh";

/** Getter -> panel push types whose listeners re-read it. */
export const PANEL_PUSH: Record<string, string[]> = {
  get_appearance: ["appearance_changed"], // AppearanceContext re-reads get_appearance
  get_models: ["models_updated"], // modelsCatalogCache
  get_models_catalog: ["models_updated"], // modelsCatalogCache
  has_any_api_key: ["models_updated"], // useHasApiKey
  list_uefn_plugins: ["uefn_plugins_changed"], // StoreTab, plugin store actions
  list_workflows: ["graphs_changed"], // AutomationsView
};

/** Getter -> agent event types whose listeners re-read it. */
export const AGENT_EVENT: Record<string, string[]> = {
  list_all_conversations: ["chats_changed", "duckies_changed"], // useChatFolders, chatReferenceStore
  list_folders: ["chats_changed"], // useChatFolders
};

/** Push the existing update events for these stale getters (each event once). */
export function refreshStale(stale: readonly string[]): void {
  const panel = new Set<string>();
  const agent = new Set<string>();
  for (const name of stale) {
    for (const type of PANEL_PUSH[name] ?? []) panel.add(type);
    for (const type of AGENT_EVENT[name] ?? []) agent.add(type);
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
