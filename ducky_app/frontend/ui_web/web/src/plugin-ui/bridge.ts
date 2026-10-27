/**
 * Host ↔ plugin iframe postMessage bridge.
 *
 * To add a method: put one entry in BRIDGE_HANDLERS. No other file needs changing.
 */

import { BRIDGE_CHANNEL } from "./constants";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { getDirectTransport } from "../remote/directTransport";
import { pluginPrefsKey } from "../hooks/pluginPrefsStorage";
import { getPluginThemeVars } from "./pluginTheme";
import { alertRefusedWrite, refusedFromResult } from "./scopeAlerts";
import { pluginUiTabId, type BridgeRequest, type BridgeResponse } from "./types";

export type BridgeContext = {
  pluginId: string;
  panelId: string;
  /** Full editor tab id — pane identity; differs from pluginUiTabId for instance tabs. */
  tabId?: string;
  version?: number;
  /** Project-relative path when this iframe is a file editor (PluginFilePane). */
  filePath?: string;
  /** The hosting iframe — used to translate in-iframe rects to app-page coords. */
  iframe?: HTMLIFrameElement | null;
};

type BridgeHandler = (
  ctx: BridgeContext,
  params: Record<string, unknown>,
) => unknown | Promise<unknown>;

/** Same per-account localStorage bag as usePluginUiPrefs. */
function readPluginPrefs(pluginId: string): Record<string, unknown> {
  try {
    const raw = localStorage.getItem(pluginPrefsKey());
    if (!raw) return {};
    const all = JSON.parse(raw) as Record<string, Record<string, unknown>>;
    const slot = all[pluginId];
    return slot && typeof slot === "object" && !Array.isArray(slot) ? { ...slot } : {};
  } catch {
    return {};
  }
}

/** One write at a time per plugin, so two quick saves never drop each other's key. */
const prefWrites = new Map<string, Promise<void>>();

/** Save one key into the app's copy of the plugin's prefs. Ducky writes some keys itself
 * (the Browser's agentSites, from the person's answer in a chat): sending the page's whole
 * local copy wiped them on its next save (every page the Browser loaded). */
function savePrefOnDisk(pluginId: string, id: string, value: unknown): void {
  const api = getApi();
  if (!api?.plugin_prefs_set) return;
  const run = (prefWrites.get(pluginId) ?? Promise.resolve()).then(async () => {
    let disk: Record<string, unknown> | null = null;
    try {
      const all = await api.plugin_prefs_get_all?.();
      if (all?.ok) disk = { ...(all.prefs?.[pluginId] ?? {}) };
    } catch {
      /* fall back to the page's copy */
    }
    const slot = disk ?? readPluginPrefs(pluginId);
    if (value === undefined) delete slot[id];
    else slot[id] = value;
    await api.plugin_prefs_set!(pluginId, slot).catch(() => undefined);
    if (!disk) return;
    try {  // the page's copy catches up with what Ducky wrote
      const raw = localStorage.getItem(pluginPrefsKey());
      const all = raw ? (JSON.parse(raw) as Record<string, Record<string, unknown>>) : {};
      localStorage.setItem(pluginPrefsKey(), JSON.stringify({ ...all, [pluginId]: slot }));
    } catch {
      /* the next hydrate brings it */
    }
  });
  prefWrites.set(pluginId, run.catch(() => undefined));
}

function writePluginPref(pluginId: string, id: string, value: unknown): void {
  try {
    const key = pluginPrefsKey();
    const raw = localStorage.getItem(key);
    const all = raw ? (JSON.parse(raw) as Record<string, Record<string, unknown>>) : {};
    const base = all && typeof all === "object" && !Array.isArray(all) ? all : {};
    const slot = { ...(base[pluginId] ?? {}) };
    if (
      typeof value === "boolean" ||
      typeof value === "string" ||
      typeof value === "number"
    ) {
      slot[id] = value;
    } else if (value === null) {
      delete slot[id];
    } else {
      throw new Error("prefs.set value must be boolean | string | number | null");
    }
    const next = { ...base, [pluginId]: slot };
    localStorage.setItem(key, JSON.stringify(next));
    savePrefOnDisk(pluginId, id, slot[id]);
    window.dispatchEvent(
      new CustomEvent("uefn-plugin-prefs", { detail: { pluginId } }),
    );
  } catch (err) {
    throw err instanceof Error ? err : new Error(String(err));
  }
}

const PAGE_TAB = /^[A-Za-z0-9_-]{1,24}$/;

/** Pane identity is ALWAYS under the plugin tab id — a plugin can only drive its own panes.
 * `params.tab` picks one of the page tabs inside it: `<tab id>::<page tab>`. */
function browserPaneId(ctx: BridgeContext, params?: Record<string, unknown>): string {
  const base = ctx.tabId || pluginUiTabId(ctx.pluginId, ctx.panelId);
  const tab = typeof params?.tab === "string" ? params.tab : "";
  return PAGE_TAB.test(tab) ? `${base}::${tab}` : base;
}

/** The editor tab's own panes: its id, or its page tabs `<tab id>::<page tab>`. */
export function ownsBrowserPane(tabId: string, paneId: string): boolean {
  return paneId === tabId || paneId.startsWith(`${tabId}::`);
}

/** "Open in new tab": ChatView stashes the popup URL for the freshly opened
 * instance tab; that tab's `browser.open` consumes it instead of its default. */
const paneInitialUrls = new Map<string, string>();

export function setBrowserPaneInitialUrl(tabId: string, url: string): void {
  paneInitialUrls.set(tabId, url);
}

function takeBrowserPaneInitialUrl(tabId: string): string {
  const url = paneInitialUrls.get(tabId) ?? "";
  paneInitialUrls.delete(tabId);
  return url;
}

/** Focus windows carry ?wid=… — the native pane must attach to the hosting OS window. */
function hostWindowWid(): string {
  try {
    return new URLSearchParams(window.location.search).get("wid") ?? "";
  } catch {
    return "";
  }
}

function asFiniteNumber(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Native-pane bounds tracking. The plugin reports its content rect once per
 * layout change; we remember it as INSET MARGINS from the iframe edges so the
 * host can re-derive the absolute rect every frame from the live iframe rect —
 * window resizes and layout shifts track at rAF speed with no bridge round-trip.
 */
type PaneInset = { left: number; top: number; right: number; bottom: number; visible: boolean };
const paneInsets = new Map<string, { iframe: HTMLIFrameElement; inset: PaneInset }>();
const lastSentBounds = new Map<string, { key: string; at: number }>();

/** Resend an unchanged rect once a minute (self-heals native drift, missed messages).
 * A rect changed by >= 1 px sends at once; tab mounts clear the dedupe. */
const BOUNDS_HEARTBEAT_MS = 60_000;

/**
 * When > 0, native WebView2 panes stay hidden so HTML modals / autocomplete
 * (main window or plugin iframe) paint above the OS-level browser control.
 */
let browserUiCoverDepth = 0;

/** Hide/show all browser panes under HTML chrome (settings, address suggestions). */
export function setBrowserUiCover(cover: boolean): void {
  browserUiCoverDepth += cover ? 1 : -1;
  if (browserUiCoverDepth < 0) browserUiCoverDepth = 0;
  if (isRemote()) return;
  for (const tabId of paneInsets.keys()) {
    lastSentBounds.delete(tabId);
    pushBrowserPaneBounds(tabId);
  }
  // Also hide panes that have no inset yet (boot.js settings over any tab).
  const api = getApi();
  if (api?.browser_pane_list && browserUiCoverDepth > 0) {
    void api.browser_pane_list().then((res) => {
      for (const p of res?.panes ?? []) {
        const id = p.pane_id;
        if (!id || paneInsets.has(id)) continue;
        void api.browser_pane_set_bounds?.(id, 0, 0, 0, 0, 0, 0, false);
      }
    });
  }
}

/** Clear stuck cover depth and re-show any mounted browser pane (never hide_all). */
export function forceClearBrowserUiCover(): void {
  browserUiCoverDepth = 0;
  for (const [tabId, entry] of paneInsets) {
    entry.inset.visible = true;
    lastSentBounds.delete(tabId);
    pushBrowserPaneBounds(tabId);
  }
}

/**
 * Escape hatch when shell.boot / browser overlays steal all clicks (left dock
 * locked, black content). Remove overlays, clear cover, hide stray panes, then
 * re-pin only panes that still have a mounted iframe inset.
 */
export function scrubPluginShellInterference(): void {
  try {
    document
      .querySelectorAll(".browser-settings-overlay, .browser-header-menu")
      .forEach((el) => el.remove());
  } catch {
    /* ignore */
  }
  browserUiCoverDepth = 0;
  if (isRemote()) return;
  const api = getApi();
  // First bury every native pane so a mis-sized WebView cannot sit on the dock.
  if (typeof api?.browser_pane_hide_all === "function") {
    void api.browser_pane_hide_all().catch(() => undefined);
  } else if (api?.browser_pane_list) {
    void api.browser_pane_list().then((res) => {
      for (const p of res?.panes ?? []) {
        const id = p.pane_id;
        if (!id) continue;
        void api.browser_pane_set_bounds?.(id, 0, 0, 0, 0, 0, 0, false);
      }
    });
  }
  // Then restore only panes whose iframe is still mounted AND marked visible.
  for (const [tabId, entry] of paneInsets) {
    if (!entry.iframe?.isConnected || !entry.inset.visible) continue;
    lastSentBounds.delete(tabId);
    pushBrowserPaneBounds(tabId);
  }
}

declare global {
  interface Window {
    __duckySetBrowserUiCover?: (cover: boolean) => void;
    __duckyScrubPluginShell?: () => void;
  }
}

if (typeof window !== "undefined") {
  window.__duckySetBrowserUiCover = setBrowserUiCover;
  window.__duckyScrubPluginShell = scrubPluginShellInterference;
}

/** Anything of Ducky's own UI drawn over a pane (a dialog and its dim layer, a menu, a
 * tooltip or hover card): the native pane can't go under HTML, so it hides while a
 * snapshot of the page stands in for it. */
const FLOATING_SELECTOR =
  '[role="dialog"],[role="alertdialog"],[role="tooltip"],[role="menu"],[role="listbox"],' +
  '[aria-modal="true"],[data-radix-popper-content-wrapper],.modal-backdrop';
const COVER_CHECK_MS = 120;
/** How long the live page may stay up while its snapshot is on the way. */
const SNAPSHOT_WAIT_MS = 300;

type CoverState = { covered: boolean; checkedAt: number; since: number; shot: "none" | "pending" | "shown" };
const coverStates = new Map<string, CoverState>();

export function paneCoveredByHtml(
  iframe: HTMLIFrameElement, x: number, y: number, w: number, h: number,
): boolean {
  if (w < 8 || h < 8) return false;
  for (const px of [x + 3, x + w * 0.25, x + w / 2, x + w * 0.75, x + w - 3]) {
    for (const py of [y + 3, y + h / 2, y + h - 3]) {
      const hit = document.elementFromPoint(px, py);
      if (hit && hit !== iframe && !hit.contains(iframe)) return true;
    }
  }
  for (const el of document.querySelectorAll<HTMLElement>(FLOATING_SELECTOR)) {
    if (el.contains(iframe)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.right <= x || r.left >= x + w || r.bottom <= y || r.top >= y + h) continue;
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) < 0.05) continue;
    return true;
  }
  return false;
}

/** A box in a panel's own coordinates moved onto the app page (the iframe's offset added). */
export function panelBoxOnPage(iframe: HTMLIFrameElement | null | undefined, box: unknown): Record<string, number> | null {
  const b = (box && typeof box === "object" ? box : null) as Record<string, unknown> | null;
  if (!b || !iframe) return null;
  const frame = iframe.getBoundingClientRect();
  return { left: frame.left + Number(b.left), top: frame.top + Number(b.top), width: Number(b.width), height: Number(b.height) };
}

function postPaneSnapshot(iframe: HTMLIFrameElement, paneId: string, data: string): void {
  iframe.contentWindow?.postMessage(
    { channel: BRIDGE_CHANNEL, event: { type: "browser_pane_snapshot", pane_id: paneId, data } }, "*");
}

/** False while HTML covers the pane and its snapshot is up (or took too long). */
function showPaneUncovered(paneId: string, iframe: HTMLIFrameElement, x: number, y: number, w: number, h: number): boolean {
  const now = Date.now();
  let st = coverStates.get(paneId);
  if (!st) {
    st = { covered: false, checkedAt: 0, since: 0, shot: "none" };
    coverStates.set(paneId, st);
  }
  if (now - st.checkedAt >= COVER_CHECK_MS) {
    st.checkedAt = now;
    const covered = paneCoveredByHtml(iframe, x, y, w, h);
    if (covered && !st.covered) {
      st.since = now;
      st.shot = "pending";
      const state = st;
      void getApi()?.browser_pane_snapshot?.(paneId)
        .then((res) => {
          if (state.covered && res?.ok && res.data) {
            postPaneSnapshot(iframe, paneId, res.data);
            state.shot = "shown";
            lastSentBounds.delete(paneId);
          }
        })
        .catch(() => undefined);
    } else if (!covered && st.covered) {
      if (st.shot === "shown") setTimeout(() => postPaneSnapshot(iframe, paneId, ""), 80);
      st.shot = "none";
    }
    st.covered = covered;
  }
  if (!st.covered) return true;
  return st.shot === "pending" && now - st.since < SNAPSHOT_WAIT_MS;
}

/** Re-pin every pane this editor tab owns (its page tabs, or its one pane). */
export function pushOwnBrowserPanes(tabId: string, iframe?: HTMLIFrameElement | null): void {
  let any = false;
  for (const id of paneInsets.keys()) {
    if (id !== tabId && ownsBrowserPane(tabId, id)) {
      any = true;
      pushBrowserPaneBounds(id, iframe);
    }
  }
  if (!any) pushBrowserPaneBounds(tabId, iframe);
}

/** Re-pin the native pane from the current iframe rect + last reported inset. */
export function pushBrowserPaneBounds(tabId: string, iframe?: HTMLIFrameElement | null): void {
  if (isRemote()) return;
  const el = iframe && iframe.isConnected ? iframe : paneInsets.get(tabId)?.iframe;
  if (!el || !el.isConnected) return;
  // If the plugin's first browser.bounds failed, still pin to the full iframe —
  // otherwise rAF silently no-ops forever (URL updates, content stays black).
  let entry = paneInsets.get(tabId);
  if (!entry) {
    entry = {
      iframe: el,
      inset: { left: 0, top: 0, right: 0, bottom: 0, visible: true },
    };
    paneInsets.set(tabId, entry);
  }
  // Insets survive tab switches; rebind them to the freshly mounted iframe.
  if (el !== entry.iframe) entry.iframe = el;
  const api = getApi();
  if (!api?.browser_pane_set_bounds) return;
  const rect = el.getBoundingClientRect();
  const iw = el.clientWidth || rect.width;
  const ih = el.clientHeight || rect.height;
  if (iw < 4 || ih < 4) return;
  const { left, top, right, bottom, visible: insetVisible } = entry.inset;
  const x = rect.x + left;
  const y = rect.y + top;
  const w = Math.max(iw - left - right, 0);
  const h = Math.max(ih - top - bottom, 0);
  const visible = browserUiCoverDepth > 0 ? false : insetVisible && showPaneUncovered(tabId, el, x, y, w, h);
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const key = [x, y, w, h, vw, vh, visible ? 1 : 0]
    .map((n) => Math.round(Number(n)))
    .join(",");
  const last = lastSentBounds.get(tabId);
  const now = Date.now();
  if (last && last.key === key && now - last.at < BOUNDS_HEARTBEAT_MS) return;
  lastSentBounds.set(tabId, { key, at: now });
  void api
    .browser_pane_set_bounds(tabId, x, y, w, h, vw, vh, visible)
    .then((res) => {
      // Remote deny / missing method → undefined. Do not retry every frame.
      if (res === undefined) return;
      // "pane not open" during boot must not poison the dedupe — retry next frame.
      if (!res || res.ok === false) lastSentBounds.delete(tabId);
    })
    .catch(() => lastSentBounds.delete(tabId));
}

/** Mount generation — hides scheduled on unmount must not win over a remount. */
const paneMountGen = new Map<string, number>();

/** Tab became active again: bump gen, force visible, clear dedupe. */
export function beginBrowserPaneMount(tabId: string): void {
  paneMountGen.set(tabId, (paneMountGen.get(tabId) ?? 0) + 1);
  const entry = paneInsets.get(tabId);
  if (entry) entry.inset.visible = true;
  lastSentBounds.delete(tabId);
  // Stuck cover depth from address-bar autocomplete left every pane black.
  browserUiCoverDepth = 0;
  if (isRemote()) return;
  // Immediately hide every other native pane so a stale YouTube layer cannot
  // sit on top of this tab's toolbar while we wait for the first bounds tick.
  // Page tabs that were on screen when this tab was left come back on screen.
  for (const id of shownBeforeUnmount.get(tabId) ?? []) {
    const own = paneInsets.get(id);
    if (own) own.inset.visible = true;
    lastSentBounds.delete(id);
  }
  shownBeforeUnmount.delete(tabId);
  const api = getApi();
  if (api?.browser_pane_list) {
    void api.browser_pane_list().then((res) => {
      for (const p of res?.panes ?? []) {
        const id = p.pane_id;
        if (!id || ownsBrowserPane(tabId, id)) continue;
        void api.browser_pane_set_bounds?.(id, 0, 0, 0, 0, 0, 0, false);
      }
    });
  }
}

/** Page tabs (`<tab>::<page tab>`) that were visible when their editor tab was left. */
const shownBeforeUnmount = new Map<string, string[]>();

/** Unmount (tab switch): hide native pane, but ignore the hide if we remounted. */
export function hideBrowserPaneOnUnmount(tabId: string): void {
  const hideGen = paneMountGen.get(tabId) ?? 0;
  const subs = [...paneInsets.keys()].filter((id) => id !== tabId && ownsBrowserPane(tabId, id));
  shownBeforeUnmount.set(tabId, subs.filter((id) => paneInsets.get(id)?.inset.visible));
  for (const id of [tabId, ...subs]) {
    lastSentBounds.delete(id);
    const entry = paneInsets.get(id);
    if (entry) entry.inset.visible = false;
  }
  if (isRemote()) return;
  for (const id of [tabId, ...subs]) {
    void getApi()
      ?.browser_pane_set_bounds?.(id, 0, 0, 0, 0, 0, 0, false)
      ?.finally(() => {
        // Late hide after remount would leave a blank/brown pane — re-pin.
        if ((paneMountGen.get(tabId) ?? 0) !== hideGen) {
          pushBrowserPaneBounds(id);
        }
      });
  }
}

/** Forget all tracking state (tab actually closed). */
export function clearBrowserPaneBounds(tabId: string): void {
  for (const id of [...paneInsets.keys(), ...coverStates.keys()]) {
    if (!ownsBrowserPane(tabId, id)) continue;
    paneInsets.delete(id);
    lastSentBounds.delete(id);
    coverStates.delete(id);
  }
  lastSentBounds.delete(tabId);
  paneMountGen.delete(tabId);
  shownBeforeUnmount.delete(tabId);
}

/** tabId → push subscription for plugin iframes */
export const pluginPushSubscriptions = new Map<
  string,
  { pluginId: string; types: Set<string> }
>();

/** Events the host tells panels itself, in their documented shape: the scope bar sends
 * `plugin_scope_changed` with `reason` and `keys`. The raw app push of that name (every
 * sync round, lock and mode change, other plugins' too) carries no `reason`, so a panel
 * that subscribed to it read each one as a switch and started over. Live messages, peers,
 * holds, groupmates' live states and the group's shared values reach only their own
 * plugin's panels (`live.message`, `live.peers`, `live.holds`, `live.memberState`,
 * `live.shared`), and the peer
 * connections' commands (`rtc_cmd`: offers and sealed bytes) never reach a panel. */
const HOST_TOLD_EVENTS = new Set(["plugin_scope_changed", "live_message", "live_peers", "live_holds",
  "live_member_state", "live_shared", "rtc_cmd"]);

export function shouldForwardPluginPush(
  tabId: string,
  pluginId: string,
  eventType: string,
): boolean {
  if (HOST_TOLD_EVENTS.has(eventType)) return false;
  const sub = pluginPushSubscriptions.get(tabId);
  if (!sub || sub.pluginId !== pluginId) return false;
  if (sub.types.size === 0) return true;
  return sub.types.has(eventType);
}

const viewId = (v: unknown) => (typeof v === "string" ? v.replace(/[^A-Za-z0-9._:-]/g, "").slice(0, 64) : "");

/** A pointer, selection, view or tab click from the host script, sent to teammates live in
 * the group: where it is (an anchor selector), a pointer's point as fractions of that
 * element, a view's id, the plugin's name for its screen (`at`) and scroll positions.
 * Pointers, selections and the leader's views may drop (the next replaces them); tab clicks
 * and a member's own moves go reliably. A member's own move asks the leader (`ask`, to its
 * device `to`); `o` names the page that did it first. */
function liveHost(ctx: BridgeContext, topic: "ducky.pointer" | "ducky.select" | "ducky.view" | "ducky.nav",
  params: Record<string, unknown>) {
  const anchor = typeof params.anchor === "string" ? params.anchor.slice(0, 400) : "";
  const frac = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0);
  const o = typeof params.o === "string" ? params.o.replace(/[^A-Za-z0-9:]/g, "").slice(0, 40) : "";
  const ask = params.ask ? { ask: 1 } : {};
  const to = params.ask && typeof params.to === "string" ? params.to.slice(0, 80) : "";
  const v = params.v === undefined ? {} : { v: viewId(params.v) };
  let data: Record<string, unknown>;
  if (topic === "ducky.view") {
    const scroll = Array.isArray(params.scroll) ? params.scroll.slice(0, 8).flatMap((s) => {
      const row = s as { anchor?: unknown; top?: unknown; left?: unknown };
      return typeof row?.anchor === "string" && typeof row.top === "number" && typeof row.left === "number"
        ? [{ anchor: row.anchor.slice(0, 400), top: Math.round(row.top), left: Math.round(row.left) }] : [];
    }) : [];
    const at = typeof params.at === "string" ? params.at.slice(0, 400) : "";
    data = { v: viewId(params.v), ...(at ? { at } : {}), scroll, ...ask };
  } else {
    if (!anchor) return { ok: false };
    data = topic === "ducky.pointer" ? { anchor, fx: frac(params.fx), fy: frac(params.fy), v: viewId(params.v) }
      : topic === "ducky.nav" ? { anchor, ...v, ...(o ? { o } : {}), ...ask } : { anchor };
  }
  const fast = (topic === "ducky.pointer" || topic === "ducky.select" || topic === "ducky.view") && !params.ask;
  void getApi()?.plugin_live_send?.(ctx.pluginId, topic, data, to, fast)?.catch(() => {});
  return { ok: true };
}

/**
 * Host data service (docs + files in the active scope, Personal or a shared space).
 * One panel API dispatcher; the host always uses the iframe's own plugin id.
 */
export const HOST_DATA_OPS = [
  "scope.get",
  "data.get",
  "data.put",
  "data.list",
  "data.delete",
  "files.put",
  "files.get",
  "files.list",
  "files.delete",
] as const;

function hostDataHandler(op: string): BridgeHandler {
  return async (ctx, params) => {
    const api = getApi();
    if (!api?.plugin_data) throw new Error("plugin data unavailable");
    const out = await api.plugin_data(ctx.pluginId, op, params);
    const refused = refusedFromResult(ctx.pluginId, out);
    if (refused) alertRefusedWrite(refused);
    if (out?.ok === false && out.error) throw new Error(String(out.error));
    return out;
  };
}

/**
 * Allowlisted bridge methods. Add new capabilities here only.
 * Keys are what the plugin calls via `postMessage({ method: "…" })`.
 */
export const BRIDGE_HANDLERS: Record<string, BridgeHandler> = {
  "plugin.info": (ctx) => ({
    pluginId: ctx.pluginId,
    panelId: ctx.panelId,
    version: ctx.version ?? null,
    filePath: ctx.filePath ?? null,
  }),
  /** Appearance CSS custom properties (no leading --). Live updates: appearance_theme push. */
  "theme.get": () => ({ vars: getPluginThemeVars() }),
  /** One of this plugin's own files (or the UI kit's) from the desktop, for a page the
   * phone panel built over the PC link: its later fetches and media (public/plugin-frame.js). */
  "desktop.file": async (ctx, params) => {
    const path = new URL(String(params.path || ""), "https://desktop.invalid").pathname;
    const query = String(params.path || "").split("?")[1];
    if (!path.startsWith(`/plugin-ui/${ctx.pluginId}/`) && !path.startsWith("/plugin-ui/_kit/")) {
      throw new Error("not one of this plugin's files");
    }
    const transport = getDirectTransport();
    if (!transport) throw new Error("no PC link");
    const res = await transport.fetchBlob(query ? `${path}?${query}` : path);
    return { status: res.status, type: res.headers.get("Content-Type") || "", body: await res.arrayBuffer() };
  },
  /** Sent by the error reporter the host puts in every panel page: uncaught errors,
   * rejected promises and console.error, kept per plugin for ducky_plugin_errors. */
  "panel.error": async (ctx, params) => {
    const text = (v: unknown, max: number) => String(v ?? "").slice(0, max);
    await getApi()?.plugin_panel_error?.(
      ctx.pluginId,
      ctx.panelId,
      text(params.kind, 16),
      text(params.message, 1000),
      text(params.stack, 4000),
    );
    return { ok: true };
  },
  // Native WebView2 browser pane (backend browser_overlay). Real Chromium pinned
  // over the panel's reported rect — no X-Frame-Options limits, and no pywebview
  // JS bridge is injected into the visited page.
  "browser.open": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_pane_open) throw new Error("browser panes unavailable");
    const paneId = browserPaneId(ctx, params);
    // A pending "open in new tab" URL wins over the plugin's own start URL.
    const url = takeBrowserPaneInitialUrl(paneId) || (typeof params.url === "string" ? params.url : "");
    lastSentBounds.delete(paneId);
    return await api.browser_pane_open(paneId, url, hostWindowWid());
  },
  "browser.bounds": (ctx, params) => {
    // Params are CSS px in the IFRAME's coordinate space. Store as edge insets so
    // pushBrowserPaneBounds can re-derive the absolute rect every frame.
    const tabId = browserPaneId(ctx, params);
    const iframe = ctx.iframe;
    if (!iframe) throw new Error("browser panes unavailable");
    const x = asFiniteNumber(params.x);
    const y = asFiniteNumber(params.y);
    const w = asFiniteNumber(params.width);
    const h = asFiniteNumber(params.height);
    const iw = iframe.clientWidth;
    const ih = iframe.clientHeight;
    paneInsets.set(tabId, {
      iframe,
      inset: {
        left: x,
        top: y,
        right: Math.max(iw - (x + w), 0),
        bottom: Math.max(ih - (y + h), 0),
        visible: params.visible !== false,
      },
    });
    pushBrowserPaneBounds(tabId, iframe);
    return { ok: true };
  },
  "browser.navigate": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_pane_navigate) throw new Error("browser panes unavailable");
    const url = typeof params.url === "string" ? params.url : "";
    if (!url) throw new Error("browser.navigate requires url");
    return await api.browser_pane_navigate(browserPaneId(ctx, params), url);
  },
  "browser.command": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_pane_command) throw new Error("browser panes unavailable");
    const command = typeof params.command === "string" ? params.command : "";
    return await api.browser_pane_command(browserPaneId(ctx, params), command);
  },
  "browser.state": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_pane_state) throw new Error("browser panes unavailable");
    return await api.browser_pane_state(browserPaneId(ctx, params));
  },
  /** This tab's open page tabs (after a reload the page reattaches to them). */
  "browser.tabs": async (ctx) => {
    const api = getApi();
    if (!api?.browser_pane_list) throw new Error("browser panes unavailable");
    const base = browserPaneId(ctx);
    const res = await api.browser_pane_list();
    return { ok: true, panes: (res?.panes ?? []).filter((p) => p.pane_id && ownsBrowserPane(base, p.pane_id)) };
  },
  "browser.close": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_pane_close) throw new Error("browser panes unavailable");
    const paneId = browserPaneId(ctx, params);
    clearBrowserPaneBounds(paneId);
    return await api.browser_pane_close(paneId);
  },
  /** Hide native panes under HTML overlays (autocomplete / nested dialogs). */
  "browser.ui_cover": (_ctx, params) => {
    setBrowserUiCover(params.cover !== false && params.cover !== 0);
    return { ok: true, covered: browserUiCoverDepth > 0 };
  },
  /** Open Browser settings modal (shell.boot) above the OS WebView2 layer. */
  "browser.open_settings": () => {
    window.dispatchEvent(new CustomEvent("ducky:browser-settings"));
    return { ok: true };
  },
  "browser.runtime_info": async () => {
    const api = getApi();
    if (!api?.browser_runtime_info) throw new Error("browser_runtime_info unavailable");
    return await api.browser_runtime_info();
  },
  "browser.site_security": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_site_security) throw new Error("browser_site_security unavailable");
    return await api.browser_site_security(browserPaneId(ctx, params));
  },
  "prefs.get": (ctx, params) => {
    const prefs = readPluginPrefs(ctx.pluginId);
    const id = typeof params.id === "string" ? params.id : "";
    if (!id) return { prefs };
    return { id, value: prefs[id] ?? null };
  },
  "prefs.set": (ctx, params) => {
    const id = typeof params.id === "string" ? params.id : "";
    if (!id) throw new Error("prefs.set requires id");
    writePluginPref(ctx.pluginId, id, params.value ?? null);
    return { ok: true };
  },
  "ui.confirm": async (ctx, params) => {
    // A plugin's "are you sure?" is Ducky's own confirm modal, not a bar inside the panel.
    const { confirmFromHost } = await import("../contexts/ConfirmModalContext");
    const message = typeof params.message === "string" ? params.message.trim() : "";
    if (!message) throw new Error("ui.confirm requires message");
    const result = await confirmFromHost({
      title: typeof params.title === "string" && params.title.trim() ? params.title.trim() : ctx.pluginId,
      message,
      confirmLabel: typeof params.confirm === "string" && params.confirm.trim() ? params.confirm.trim() : undefined,
      cancelLabel: typeof params.cancel === "string" && params.cancel.trim() ? params.cancel.trim() : undefined,
      danger: params.danger === true,
    });
    return { confirmed: result === true };
  },
  "ui.alert": async (ctx, params) => {
    const { alertFromHost } = await import("../contexts/ConfirmModalContext");
    const message = typeof params.message === "string" ? params.message.trim() : "";
    if (!message) throw new Error("ui.alert requires message");
    await alertFromHost({
      title: typeof params.title === "string" && params.title.trim() ? params.title.trim() : ctx.pluginId,
      message,
      okLabel: typeof params.ok === "string" && params.ok.trim() ? params.ok.trim() : undefined,
    });
    return { ok: true };
  },
  /** Ducky's own model picker: ``{purpose?: "video"|"chat", current?, title?, extra?: [{id, name, group, note?, vision?}], anchor?: {left, top, width, height}}`` → ``{model, name}`` or ``{model: ""}`` when cancelled. With ``anchor`` (a box in the panel) it drops down from there instead of opening a dialog. */
  "ui.pickModel": async (ctx, params) => {
    if (isRemote()) throw new Error("Pick models on the PC that runs Ducky.");
    const { pickModelFromHost } = await import("./PluginModelPickerHost");
    const picked = await pickModelFromHost({
      purpose: typeof params.purpose === "string" ? params.purpose : "chat",
      current: typeof params.current === "string" ? params.current : "",
      title: typeof params.title === "string" ? params.title : "",
      extra: params.extra,
      hide: params.hide,
      anchor: panelBoxOnPage(ctx.iframe, params.anchor),
    });
    return picked ?? { model: "", name: "" };
  },
  /** Record from the microphone in the host window (panels have no microphone access). */
  "audio.recordStart": async (ctx) => {
    if (isRemote()) throw new Error("Record on the PC that runs Ducky.");
    const { startRecording } = await import("./hostRecorder");
    return await startRecording(ctx.pluginId);
  },
  "audio.recordStop": async (ctx, params) => {
    const { stopRecording } = await import("./hostRecorder");
    return await stopRecording(ctx.pluginId, typeof params.id === "string" ? params.id : "");
  },
  "audio.recordCancel": async (ctx, params) => {
    const { cancelRecording } = await import("./hostRecorder");
    return cancelRecording(ctx.pluginId, typeof params.id === "string" ? params.id : "");
  },
  "models.list": async () => {
    // The chat models the person can pick, so a plugin can offer the same choice its
    // llm.complete call accepts (ids only; keys and prices stay with the app).
    const api = getApi();
    if (!api?.get_models_catalog) throw new Error("get_models_catalog unavailable");
    const catalog = await api.get_models_catalog(false);
    return {
      default_model: catalog?.agent_model || catalog?.default_model || "",
      models: (catalog?.models || []).map((m) => ({
        id: m.id,
        name: m.name,
        provider: m.provider,
        provider_key: m.provider_key || "",
        is_local: !!m.is_local,
        supports_vision: !!m.supports_vision,
      })),
    };
  },
  "llm.complete": async (ctx, params) => {
    const { pluginLlmCompleteAsync } = await import("./pluginLlmAsync");
    const system = typeof params.system === "string" ? params.system : "";
    const user = typeof params.user === "string" ? params.user : "";
    const model = typeof params.model === "string" ? params.model : "";
    if (!user.trim()) throw new Error("llm.complete requires user");
    return await pluginLlmCompleteAsync(ctx.pluginId, system, user, model);
  },
  "cache.get": async (ctx, params) => {
    const api = getApi();
    if (!api?.plugin_cache_get) throw new Error("plugin_cache_get unavailable");
    const key = typeof params.key === "string" ? params.key : "";
    if (!key) throw new Error("cache.get requires key");
    return await api.plugin_cache_get(ctx.pluginId, key);
  },
  "cache.set": async (ctx, params) => {
    const api = getApi();
    if (!api?.plugin_cache_set) throw new Error("plugin_cache_set unavailable");
    const key = typeof params.key === "string" ? params.key : "";
    if (!key) throw new Error("cache.set requires key");
    const data =
      params.data && typeof params.data === "object" && !Array.isArray(params.data)
        ? (params.data as Record<string, unknown>)
        : {};
    return await api.plugin_cache_set(ctx.pluginId, key, data);
  },
  "cache.clear": async (ctx, params) => {
    const api = getApi();
    if (!api?.plugin_cache_clear) throw new Error("plugin_cache_clear unavailable");
    const key = typeof params.key === "string" ? params.key : "";
    return await api.plugin_cache_clear(ctx.pluginId, key);
  },
  ...Object.fromEntries(HOST_DATA_OPS.map((op) => [op, hostDataHandler(op)])),
  /**
   * The PC's file picker: ``{accept?: "video"|"audio"|"image"|"any", multiple?}`` →
   * ``{files: [{path, name, kind, url}]}``. A big video stays where it is: the panel passes
   * ``path`` to its backend and plays ``url`` (streamed, seekable). Uploading a 7 GB video
   * through ``plugin.call`` in chunks froze the app; nothing is copied this way.
   */
  "files.pick": async (_ctx, params) => {
    if (isRemote()) throw new Error("Pick files on the PC that runs Ducky.");
    const api = getApi();
    if (!api?.pick_workflow_files) throw new Error("file picker unavailable");
    const accept = typeof params.accept === "string" ? params.accept : "any";
    const res = await api.pick_workflow_files(accept, params.multiple === true);
    if (!res?.ok) throw new Error(res?.error || "The file picker could not open.");
    return { files: res.files ?? [] };
  },
  /** Windows folder picker (e.g. where a panel saves its exports): ``{}`` → ``{folder}`` ("" when cancelled). */
  "files.pickFolder": async () => {
    if (isRemote()) throw new Error("Pick folders on the PC that runs Ducky.");
    const api = getApi();
    if (!api?.pick_workflow_folder) throw new Error("folder picker unavailable");
    const res = await api.pick_workflow_folder();
    if (!res?.ok) throw new Error(res?.error || "The folder picker could not open.");
    return { folder: res.folder ?? "" };
  },
  /**
   * Real paths of files the person just dropped from Explorer onto this panel:
   * ``{names?: string[]}`` → ``{files: [{path, name, kind, url}]}``. The panel's own
   * DOM ``drop`` only sees names; the host saw the native drop and holds the paths.
   */
  "files.dropped": async (_ctx, params) => {
    if (isRemote()) return { files: [] };
    const api = getApi();
    if (!api?.consume_dropped_file_paths) return { files: [] };
    const raw = params.names;
    const names = Array.isArray(raw) ? raw.filter((n): n is string => typeof n === "string") : [];
    const res = await api.consume_dropped_file_paths(names);
    return { files: res?.files ?? [] };
  },
  /** Call a panel RPC registered by the plugin via ``api.register_panel_rpc``. */
  "plugin.call": async (ctx, params) => {
    const api = getApi();
    if (!api?.plugin_call) throw new Error("plugin_call unavailable");
    const method = typeof params.method === "string" ? params.method : "";
    if (!method) throw new Error("plugin.call requires method");
    const callParams =
      params.params && typeof params.params === "object" && !Array.isArray(params.params)
        ? (params.params as Record<string, unknown>)
        : {};
    return await api.plugin_call(ctx.pluginId, method, callParams);
  },
  /** Live editing on a team copy (backend/uefn_plugins/live.py): go live in a room (no room:
   * host this PC's own group), leave, and short messages to the teammates live there. */
  "live.join": async (ctx, params) =>
    (await getApi()?.plugin_live_join?.(ctx.pluginId, typeof params.room === "string" ? params.room : "")) ?? {
      ok: false,
      error: "live editing unavailable",
    },
  "live.leave": async (ctx) => (await getApi()?.plugin_live_leave?.(ctx.pluginId)) ?? { ok: true },
  "live.state": async (ctx) => (await getApi()?.plugin_live_status?.(ctx.pluginId)) ?? { supported: false, on: false },
  "live.send": async (ctx, params) => {
    const topic = typeof params.topic === "string" ? params.topic : "";
    if (topic.startsWith("ducky.")) throw new Error("ducky.* topics are the host's");
    const to = typeof params.to === "string" ? params.to : "";
    return (await getApi()?.plugin_live_send?.(ctx.pluginId, topic, params.data ?? null, to, false)) ?? {
      ok: false,
      error: "live editing unavailable",
    };
  },
  // The host's live script in every panel page: fire and forget, so pointers stay quick.
  "live.pointer": (ctx, params) => liveHost(ctx, "ducky.pointer", params),
  "live.select": (ctx, params) => liveHost(ctx, "ducky.select", params),
  "live.view": (ctx, params) => liveHost(ctx, "ducky.view", params),
  "live.nav": (ctx, params) => liveHost(ctx, "ducky.nav", params),
  /** A control pressed or let go of: the group at once, every teammate through presence. */
  "live.hold": (ctx, params) => {
    const anchor = typeof params.anchor === "string" ? params.anchor.slice(0, 400) : "";
    if (!anchor) return { ok: false };
    void getApi()?.plugin_live_hold?.(ctx.pluginId, anchor, params.on !== false,
      typeof params.t === "number" ? params.t : 0, viewId(params.v))?.catch(() => {});
    return { ok: true };
  },
  /** What teammates hold now (the page just loaded); later changes come as `live.holds`. */
  "live.holds": async (ctx) => (await getApi()?.plugin_live_holds?.(ctx.pluginId)) ?? { ok: true, holds: [] },
  /** This PC's own live state in the plugin (a camera, the tool in hand; a JSON object up
   * to 4 KB, null clears it): every groupmate's panels get it as `live.memberState`. Latest
   * wins, so call it as often as it changes (up to 25 a second go out). */
  "live.setMyState": async (ctx, params) => {
    const state = params.state ?? null;
    if (state !== null && (typeof state !== "object" || Array.isArray(state))) throw new Error("state must be an object or null");
    return (await getApi()?.plugin_live_set_my_state?.(ctx.pluginId, state as Record<string, unknown> | null))
      ?? { ok: false, error: "live editing unavailable" };
  },
  /** The group's value of one of the plugin's components (`{key, value}`: a player playing,
   * the picked clip; null drops it), through the group's leader so everyone ends on the same
   * one; it shows here at once. Every page gets `live.shared {key, value, by}`. */
  "live.share": async (ctx, params) => {
    const key = typeof params.key === "string" ? params.key : "";
    return (await getApi()?.plugin_live_share?.(ctx.pluginId, key, params.value ?? null))
      ?? { ok: false, error: "live editing unavailable" };
  },
  /** The group's values now (the page just loaded): `{values: {key: {value, by}}}`. */
  "live.shared": async (ctx) => (await getApi()?.plugin_live_shared?.(ctx.pluginId)) ?? { ok: true, values: {} },
  /** Every groupmate's live state now (the page just loaded): `{states: [{from, state}]}`. */
  "live.memberStates": async (ctx) => (await getApi()?.plugin_live_member_states?.(ctx.pluginId)) ?? { ok: true, states: [] },
  /**
   * Subscribe this pane to host push events (e.g. discord_message).
   * Events are forwarded as ``{ channel, event }`` postMessages (same as browser_pane_state).
   */
  "plugin.subscribe": (ctx, params) => {
    const tabKey = ctx.tabId || `${ctx.pluginId}:${ctx.panelId}`;
    const raw = params.types;
    const types = Array.isArray(raw)
      ? raw.filter((t): t is string => typeof t === "string" && t.trim().length > 0)
      : [];
    pluginPushSubscriptions.set(tabKey, {
      pluginId: ctx.pluginId,
      types: new Set(types),
    });
    return { ok: true, types };
  },
};

/** Methods that must not hang the plugin iframe forever (UI-thread risk). */
const BRIDGE_FAST_TIMEOUT_MS: Record<string, number> = {
  "browser.site_security": 2500,
  "browser.runtime_info": 2500,
  "browser.state": 2500,
  "browser.tabs": 2500,
  "browser.ui_cover": 1000,
};

export async function handleBridgeRequest(
  ctx: BridgeContext,
  req: BridgeRequest,
): Promise<BridgeResponse> {
  const handler = BRIDGE_HANDLERS[req.method];
  if (!handler) {
    return { channel: BRIDGE_CHANNEL, id: req.id, ok: false, error: `unknown method: ${req.method}` };
  }
  try {
    const params =
      req.params && typeof req.params === "object" && !Array.isArray(req.params)
        ? req.params
        : {};
    const timeoutMs = BRIDGE_FAST_TIMEOUT_MS[req.method];
    const work = Promise.resolve(handler(ctx, params));
    const result =
      timeoutMs == null
        ? await work
        : await Promise.race([
            work,
            new Promise<never>((_, reject) => {
              window.setTimeout(
                () => reject(new Error(`bridge handler timeout: ${req.method}`)),
                timeoutMs,
              );
            }),
          ]);
    return { channel: BRIDGE_CHANNEL, id: req.id, ok: true, result };
  } catch (err) {
    return {
      channel: BRIDGE_CHANNEL,
      id: req.id,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
