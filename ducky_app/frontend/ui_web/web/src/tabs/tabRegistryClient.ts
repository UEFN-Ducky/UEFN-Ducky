import { getApi } from "../hooks/usePanelApi";
import { boundedSet } from "../utils/boundedMap";
import { setVisibleInterval } from "../utils/visibleInterval";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent } from "../types/panel";

/** Opaque window identity: "main" for the app window, the backend-generated
 * wid (focus-<uuid>) for focus windows. NEVER a tab id — a focus window hosts
 * many tabs, and borrowing its birth-tab id misattributed ownership of every
 * other tab dragged into it. The focus param (birth tab) stays separate. */
const _params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.search);
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
      id.startsWith("changes:") ||
      id.startsWith("discord:") ||
      id.startsWith("plugin:") ||
      id.startsWith("verse-translated:"),
  );
}

let reportTimer: number | null = null;
let lastReported = new Set<string>();
/** Tabs open in THIS window right now (synchronous, unlike the debounced report). */
let localOpenTabIds = new Set<string>();
/** Opens apply in call order: bridge replies for rapid clicks can land out of order. */
let openChain: Promise<void> = Promise.resolve();
/** A registry lookup that stalls must not wedge every later open behind it. */
const FOCUS_LOOKUP_TIMEOUT_MS = 2000;
/** Recent-claim timestamps; only the last handful are ever consulted. */
const MAX_CLAIMS = 64;
const myClaimAt = new Map<string, number>();

function log(event: string, detail?: unknown): void {
  if (!import.meta.env.DEV) return;
  console.info(`[tab-registry:${WINDOW_ID}] ${event}`, detail ?? "");
}

function claim(tabId: string): void {
  log("claim", tabId);
  boundedSet(myClaimAt, tabId, Date.now(), MAX_CLAIMS);
  void getApi()?.claim_tab(tabId, WINDOW_ID);
}

// Heartbeat: closed/crashed windows never un-report, so the backend expires window
// entries that stop heartbeating (stale owners made focus_tab raise dead windows,
// which blocked opening those files anywhere). An API without the method (a partial
// panel API) skips the report instead of throwing on every visibility change.
// Every 20 s (the backend drops a window after 30 s of silence), skipped when a real
// report went out in the last 15 s.
let lastSentAt = 0;
function sendReport(ids: string[]): void {
  lastSentAt = Date.now();
  void getApi()?.report_open_tabs?.(WINDOW_ID, ids);
}
setVisibleInterval(() => {
  if (Date.now() - lastSentAt < 15_000) return;
  sendReport([...lastReported]);
}, 20_000);

const sameIds = (ids: string[]): boolean => ids.length === lastReported.size && ids.every((id) => lastReported.has(id));

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
  localOpenTabIds = new Set();
  log("releasing all tabs (window closing)");
  sendReport([]);
}

/** Immediate (non-debounced) report — call on tab CLOSE so ownership frees at
 * once: during the debounce window a click on the closed tab would still route
 * to this window instead of falling through to "open in main". */
export function reportOpenTabsNow(tabIds: string[]): void {
  localOpenTabIds = new Set(tabIds);
  const ids = registryTabIds(tabIds);
  if (reportTimer !== null) window.clearTimeout(reportTimer);
  reportTimer = null;
  if (sameIds(ids)) return;
  sendReport(ids);
  lastReported = new Set(ids);
}

/** Debounced ownership report — call whenever openTabs changes. Tabs that newly
 * appear are CLAIMED (covers drag-drop between windows, which adds tabs without an
 * explicit open call): other windows close their copy — one tab across the system. */
export function reportOpenTabs(tabIds: string[]): void {
  localOpenTabIds = new Set(tabIds);
  const ids = registryTabIds(tabIds);
  if (reportTimer !== null) window.clearTimeout(reportTimer);
  reportTimer = window.setTimeout(() => {
    reportTimer = null;
    // Same tabs as last sent: nothing to report and nothing new to claim.
    if (sameIds(ids)) return;
    sendReport(ids);
    for (const id of ids) {
      if (!lastReported.has(id)) claim(id);
    }
    lastReported = new Set(ids);
  }, 300);
}

/** VS Code single-tab rule: if another window owns the tab, raise it there;
 * otherwise open locally and claim ownership (other windows close their copies). */
export function openOrFocusTab(tabId: string, openLocally: () => void): Promise<void> {
  // Already open here: activating it needs no registry round-trip (the backend
  // excludes the requesting window anyway) and must not wait behind one.
  if (localOpenTabIds.has(tabId)) {
    log("open locally (already here)", tabId);
    openLocally();
    return Promise.resolve();
  }
  const run = openChain.then(() => focusElsewhereOrOpen(tabId, openLocally));
  openChain = run.catch(() => undefined);
  return run;
}

/** An agent's open: add the tab here without showing it, and do nothing when it is already
 *  open in this or another window. The person stays on the tab they are on. */
export async function openInBackground(tabId: string, openLocally: () => void): Promise<void> {
  if (localOpenTabIds.has(tabId)) return;
  try {
    const owner = await Promise.race([
      getApi()?.get_tab_owner?.(tabId) ?? Promise.resolve(""),
      new Promise<string>((resolve) => setTimeout(() => resolve(""), FOCUS_LOOKUP_TIMEOUT_MS)),
    ]);
    if (owner && owner !== WINDOW_ID) return;
  } catch {
    /* open it here */
  }
  openLocally();
}

async function focusElsewhereOrOpen(tabId: string, openLocally: () => void): Promise<void> {
  const api = getApi();
  if (api) {
    try {
      const res = await Promise.race([
        api.focus_tab(tabId, WINDOW_ID),
        new Promise<null>((resolve) => window.setTimeout(() => resolve(null), FOCUS_LOOKUP_TIMEOUT_MS)),
      ]);
      if (res?.ok) {
        log("focused in other window", { tabId, owner: res.window_id });
        return;
      }
      if (res === null) log("focus_tab timed out — opening locally", tabId);
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
  closeLocal: (tabId: string, byWindowId: string) => void,
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
      const tabId = ev.tab_id;
      const claimedBy = ev.window_id ?? "";
      // Events can arrive after a close/reopen or a newer ownership transfer.
      // Only the current backend owner may remove this window's tab.
      void getApi()?.get_tab_owner(tabId).then((owner) => {
        if (owner !== claimedBy || owner === WINDOW_ID || !hasTab(tabId)) return;
        log("closing local copy — claimed by other window", { tabId, by: owner });
        closeLocal(tabId, owner);
      }).catch(() => undefined);
    } else if (event.type === "tab_focus_request" && ev.window_id === WINDOW_ID) {
      log("focus request — activating", ev.tab_id);
      activateLocal(ev.tab_id);
    }
  });
}
