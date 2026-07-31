/**
 * Host ↔ plugin iframe postMessage bridge.
 *
 * To add a method: put one entry in BRIDGE_HANDLERS. No other file needs changing.
 */

import { BRIDGE_CHANNEL } from "./constants";
import { getApi } from "../hooks/usePanelApi";
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

/** Prefer reading prefs through the same localStorage key as usePluginUiPrefs. */
const PREFS_STORAGE_KEY = "uefn-plugin-ui-prefs";

function readPluginPrefs(pluginId: string): Record<string, unknown> {
  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);
    if (!raw) return {};
    const all = JSON.parse(raw) as Record<string, Record<string, unknown>>;
    const slot = all[pluginId];
    return slot && typeof slot === "object" && !Array.isArray(slot) ? { ...slot } : {};
  } catch {
    return {};
  }
}

function writePluginPref(pluginId: string, id: string, value: unknown): void {
  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);
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
    localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(next));
    const api = getApi();
    if (api?.plugin_prefs_set) {
      void api.plugin_prefs_set(pluginId, slot).catch(() => undefined);
    }
    window.dispatchEvent(
      new CustomEvent("uefn-plugin-prefs", { detail: { pluginId } }),
    );
  } catch (err) {
    throw err instanceof Error ? err : new Error(String(err));
  }
}

/** Pane identity is ALWAYS the plugin tab id — a plugin can only drive its own pane. */
function browserPaneId(ctx: BridgeContext): string {
  return ctx.tabId || pluginUiTabId(ctx.pluginId, ctx.panelId);
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

/** Force a resend even when the rect is unchanged — self-heals any native drift
 * (mid-resize stale-scale applies, missed messages) within half a second. */
const BOUNDS_HEARTBEAT_MS = 500;

/**
 * When > 0, native WebView2 panes stay hidden so HTML modals / autocomplete
 * (main window or plugin iframe) paint above the OS-level browser control.
 */
let browserUiCoverDepth = 0;

/** Hide/show all browser panes under HTML chrome (settings, address suggestions). */
export function setBrowserUiCover(cover: boolean): void {
  browserUiCoverDepth += cover ? 1 : -1;
  if (browserUiCoverDepth < 0) browserUiCoverDepth = 0;
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

/** Re-pin the native pane from the current iframe rect + last reported inset. */
export function pushBrowserPaneBounds(tabId: string, iframe?: HTMLIFrameElement | null): void {
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
  const visible = browserUiCoverDepth > 0 ? false : insetVisible;
  const x = rect.x + left;
  const y = rect.y + top;
  const w = Math.max(iw - left - right, 0);
  const h = Math.max(ih - top - bottom, 0);
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const key = [x, y, w, h, vw, vh, visible ? 1 : 0]
    .map((n) => Math.round(Number(n) * 10))
    .join(",");
  const last = lastSentBounds.get(tabId);
  const now = Date.now();
  if (last && last.key === key && now - last.at < BOUNDS_HEARTBEAT_MS) return;
  lastSentBounds.set(tabId, { key, at: now });
  void api
    .browser_pane_set_bounds(tabId, x, y, w, h, vw, vh, visible)
    .then((res) => {
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
  // Immediately hide every other native pane so a stale YouTube layer cannot
  // sit on top of this tab's toolbar while we wait for the first bounds tick.
  const api = getApi();
  if (api?.browser_pane_list) {
    void api.browser_pane_list().then((res) => {
      for (const p of res?.panes ?? []) {
        const id = p.pane_id;
        if (!id || id === tabId) continue;
        void api.browser_pane_set_bounds?.(id, 0, 0, 0, 0, 0, 0, false);
      }
    });
  }
}

/** Unmount (tab switch): hide native pane, but ignore the hide if we remounted. */
export function hideBrowserPaneOnUnmount(tabId: string): void {
  const hideGen = paneMountGen.get(tabId) ?? 0;
  lastSentBounds.delete(tabId);
  const entry = paneInsets.get(tabId);
  if (entry) entry.inset.visible = false;
  void getApi()
    ?.browser_pane_set_bounds?.(tabId, 0, 0, 0, 0, 0, 0, false)
    ?.finally(() => {
      // Late hide after remount would leave a blank/brown pane — re-pin.
      if ((paneMountGen.get(tabId) ?? 0) !== hideGen) {
        pushBrowserPaneBounds(tabId);
      }
    });
}

/** Forget all tracking state (tab actually closed). */
export function clearBrowserPaneBounds(tabId: string): void {
  paneInsets.delete(tabId);
  lastSentBounds.delete(tabId);
  paneMountGen.delete(tabId);
}

/** tabId → push subscription for plugin iframes */
export const pluginPushSubscriptions = new Map<
  string,
  { pluginId: string; types: Set<string> }
>();

export function shouldForwardPluginPush(
  tabId: string,
  pluginId: string,
  eventType: string,
): boolean {
  const sub = pluginPushSubscriptions.get(tabId);
  if (!sub || sub.pluginId !== pluginId) return false;
  if (sub.types.size === 0) return true;
  return sub.types.has(eventType);
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
  // Native WebView2 browser pane (backend browser_overlay). Real Chromium pinned
  // over the panel's reported rect — no X-Frame-Options limits, and no pywebview
  // JS bridge is injected into the visited page.
  "browser.open": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_pane_open) throw new Error("browser panes unavailable");
    const paneId = browserPaneId(ctx);
    // A pending "open in new tab" URL wins over the plugin's own start URL.
    const url = takeBrowserPaneInitialUrl(paneId) || (typeof params.url === "string" ? params.url : "");
    lastSentBounds.delete(paneId);
    return await api.browser_pane_open(paneId, url, hostWindowWid());
  },
  "browser.bounds": (ctx, params) => {
    // Params are CSS px in the IFRAME's coordinate space. Store as edge insets so
    // pushBrowserPaneBounds can re-derive the absolute rect every frame.
    const tabId = browserPaneId(ctx);
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
    return await api.browser_pane_navigate(browserPaneId(ctx), url);
  },
  "browser.command": async (ctx, params) => {
    const api = getApi();
    if (!api?.browser_pane_command) throw new Error("browser panes unavailable");
    const command = typeof params.command === "string" ? params.command : "";
    return await api.browser_pane_command(browserPaneId(ctx), command);
  },
  "browser.state": async (ctx) => {
    const api = getApi();
    if (!api?.browser_pane_state) throw new Error("browser panes unavailable");
    return await api.browser_pane_state(browserPaneId(ctx));
  },
  "browser.close": async (ctx) => {
    const api = getApi();
    if (!api?.browser_pane_close) throw new Error("browser panes unavailable");
    const paneId = browserPaneId(ctx);
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
  "browser.site_security": async (ctx) => {
    const api = getApi();
    if (!api?.browser_site_security) throw new Error("browser_site_security unavailable");
    return await api.browser_site_security(browserPaneId(ctx));
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
