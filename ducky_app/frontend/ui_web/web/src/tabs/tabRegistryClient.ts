import { getApi } from "../hooks/usePanelApi";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent } from "../types/panel";

/** Opaque window identity: "main" for the app window, the backend-generated
 * wid (focus-<uuid>) for focus windows. NEVER a tab id — a focus window hosts
 * many tabs, and borrowing its birth-tab id misattributed ownership of every
 * other tab dragged into it. The focus param (birth tab) stays separate. */
const _params = new URLSearchParams(window.location.search);
export const WINDOW_ID: string = _params.get("wid") || _params.get("focus") || "main";

function registryTabIds(tabIds: string[]): string[] {
  // Terminals are window-bound sessions — never registered.
  return tabIds.filter(
    (id) =>
      id.startsWith("file:") ||
      id.startsWith("chat:") ||
      id.startsWith("plan:") ||
      id.startsWith("usage:") ||
      id.startsWith("settings:") ||
      id.startsWith("discord:") ||
      id.startsWith("plugin:") ||
      id.startsWith("verse-translated:"),
  );
}

let reportTimer: number | null = null;
let lastReported = new Set<string>();
const myClaimAt = new Map<string, number>();

function log(event: string, detail?: unknown): void {
  console.info(`[tab-registry:${WINDOW_ID}] ${event}`, detail ?? "");
}

function claim(tabId: string): void {
  log("claim", tabId);
  myClaimAt.set(tabId, Date.now());
  void getApi()?.claim_tab(tabId, WINDOW_ID);
}

// Heartbeat: closed/crashed windows never un-report, so the backend expires window
// entries that stop heartbeating (stale owners made focus_tab raise dead windows,
// which blocked opening those files anywhere).
window.setInterval(() => {
  void getApi()?.report_open_tabs(WINDOW_ID, [...lastReported]);
}, 10_000);

// Best-effort ownership release when a focus window is closed by any path (OS X
// button, backend close) — the heartbeat expiry is only the crash fallback.
if (WINDOW_ID !== "main") {
  window.addEventListener("pagehide", () => releaseAllTabs());
}

/** Call IMMEDIATELY before closing this window: releases all tab ownership so other
 * windows can open these tabs at once (the heartbeat would otherwise ghost-own them
 * for up to 30s, blocking every open). */
export function releaseAllTabs(): void {
  if (reportTimer !== null) window.clearTimeout(reportTimer);
  reportTimer = null;
  lastReported = new Set();
  log("releasing all tabs (window closing)");
  void getApi()?.report_open_tabs(WINDOW_ID, []);
}

/** Immediate (non-debounced) report — call on tab CLOSE so ownership frees at
 * once: during the debounce window a click on the closed tab would still route
 * to this window instead of falling through to "open in main". */
export function reportOpenTabsNow(tabIds: string[]): void {
  const ids = registryTabIds(tabIds);
  if (reportTimer !== null) window.clearTimeout(reportTimer);
  reportTimer = null;
  void getApi()?.report_open_tabs(WINDOW_ID, ids);
  lastReported = new Set(ids);
}

/** Debounced ownership report — call whenever openTabs changes. Tabs that newly
 * appear are CLAIMED (covers drag-drop between windows, which adds tabs without an
 * explicit open call): other windows close their copy — one tab across the system. */
export function reportOpenTabs(tabIds: string[]): void {
  const ids = registryTabIds(tabIds);
  if (reportTimer !== null) window.clearTimeout(reportTimer);
  reportTimer = window.setTimeout(() => {
    reportTimer = null;
    void getApi()?.report_open_tabs(WINDOW_ID, ids);
    for (const id of ids) {
      if (!lastReported.has(id)) claim(id);
    }
    lastReported = new Set(ids);
  }, 300);
}

/** VS Code single-tab rule: if another window owns the tab, raise it there;
 * otherwise open locally and claim ownership (other windows close their copies). */
export async function openOrFocusTab(tabId: string, openLocally: () => void): Promise<void> {
  const api = getApi();
  if (api) {
    try {
      const res = await api.focus_tab(tabId, WINDOW_ID);
      if (res?.ok) {
        log("focused in other window", { tabId, owner: res.window_id });
        return;
      }
    } catch (e) {
      log("focus_tab failed — opening locally", { tabId, error: String(e) });
    }
  }
  log("open locally", tabId);
  openLocally();
  claim(tabId);
}

/** File renamed on disk: remap the open tab in place (id, path, name) instead of
 * leaving an orphaned tab pointing at the old path. */
export function installFileRenameListener(
  renameLocal: (oldId: string, newId: string, newPath: string) => void,
): () => void {
  return subscribeAgentEvents((event: AgentEvent) => {
    const ev = event as AgentEvent & { old_path?: string; new_path?: string };
    if (event.type !== "file_renamed" || !ev.old_path || !ev.new_path) return;
    const oldId = `file:${ev.old_path.toLowerCase()}`;
    const newId = `file:${ev.new_path.toLowerCase()}`;
    log("file renamed — remapping tab", { oldId, newId });
    renameLocal(oldId, newId, ev.new_path);
  });
}

/** File (or folder) deleted on disk: close any open tab pointing at it. A deleted
 * file has no editor to fall back to, and leaving the tab open keeps its verse-lsp
 * document alive — that resurrects the now-stale diagnostics in the Problems panel
 * even after they're purged. `closeDeleted` receives the lowercased tab-id of the
 * dead path (`file:<path>`); the caller closes that exact tab AND any tab whose id
 * is under it (folder delete). */
export function installFileDeleteListener(
  closeDeleted: (deletedTabId: string) => void,
): () => void {
  return subscribeAgentEvents((event: AgentEvent) => {
    const ev = event as AgentEvent & { old_path?: string };
    if (event.type !== "file_deleted" || !ev.old_path) return;
    const deletedId = `file:${ev.old_path.replace(/\\/g, "/").toLowerCase()}`;
    log("file deleted — closing tab(s)", { deletedId });
    closeDeleted(deletedId);
  });
}

/** Listen for cross-window tab events. `closeLocal` must close without prompts
 * (the registry is the single tab authority — a modal prompt would desync windows);
 * `activateLocal` focuses an already-open tab. */
export function installTabRegistryListener(
  hasTab: (tabId: string) => boolean,
  closeLocal: (tabId: string) => void,
  activateLocal: (tabId: string) => void,
): () => void {
  return subscribeAgentEvents((event: AgentEvent) => {
    const ev = event as AgentEvent & { tab_id?: string; window_id?: string };
    if (!ev.tab_id) return;
    if (event.type === "tab_claimed" && ev.window_id !== WINDOW_ID && hasTab(ev.tab_id)) {
      // Race guard: if we ALSO just claimed this tab (e.g. both windows restored it at
      // boot), don't close — better a brief duplicate than both windows losing it.
      if (Date.now() - (myClaimAt.get(ev.tab_id) ?? 0) < 2000) {
        log("claim by other window IGNORED (recent own claim)", { tabId: ev.tab_id, by: ev.window_id });
        return;
      }
      log("closing local copy — claimed by other window", { tabId: ev.tab_id, by: ev.window_id });
      closeLocal(ev.tab_id);
    } else if (event.type === "tab_focus_request" && ev.window_id === WINDOW_ID) {
      log("focus request — activating", ev.tab_id);
      activateLocal(ev.tab_id);
    }
  });
}
