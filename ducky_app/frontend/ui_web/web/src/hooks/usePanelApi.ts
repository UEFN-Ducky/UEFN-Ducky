import type { PanelApi } from "../types/panel";
import { getDirectTransport, isRemoteDuckyWindow } from "../remote/directTransport";
import { openHttpsOnThisDevice } from "../remote/openHttps";
import { callOverHttp, canCallOverHttp } from "./panelHttp";

/**
 * True in a phone, tunnel or panel-host browser: anything but the desktop's WebView2.
 * pywebview injects `window.pywebview` only after the page loads, long after the app's
 * first effects ran, so "no pywebview yet" is not "remote": every mount-once
 * `if (isRemote()) return;` took the desktop for a phone (direct Remote View offers,
 * notification clicks and the signed-out Store opener never started).
 */
export function isRemote(): boolean {
  if (typeof window === "undefined") return false;
  // A WebView2 window of this app showing another of your UEFN Duckies is that
  // Ducky's remote view, like a phone: never this desktop.
  if (isRemoteDuckyWindow()) return true;
  if (window.pywebview) return false;
  return !(window as Window & { chrome?: { webview?: unknown } }).chrome?.webview;
}

/** pywebview may set `window.pywebview.api` before methods are bound — probe a core method. */
export function isPanelApiReady(api: unknown): api is PanelApi {
  return !!api && typeof (api as PanelApi).get_listener_status === "function";
}

export function isRemoteNetworkBlip(err: unknown): boolean {
  if (err instanceof TypeError) return true;
  return typeof err === "object" && err !== null && "name" in err && String((err as { name: unknown }).name) === "AbortError";
}

export function remoteInflightKey(name: string, args: unknown[]): string {
  return `${name}:${JSON.stringify(args)}`;
}

const _inflight = new Map<string, Promise<unknown>>();

function remoteApi(): PanelApi {
  const invoke = (name: string, args: unknown[]) => {
    if (name === "open_external_url") {
      openHttpsOnThisDevice(String(args[0] || ""));
      return Promise.resolve();
    }
    if (name === "copy_text") {
      return navigator.clipboard.writeText(String(args[0] ?? "")).then(
        () => true,
        () => false,
      );
    }
    const key = remoteInflightKey(name, args);
    const hit = _inflight.get(key);
    if (hit) return hit;
    const pending = fetch("/__panel_api/" + encodeURIComponent(name), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ args }),
    })
      .then(async (r) => {
        const json = (await r.json().catch(() => ({}))) as {
          ok?: boolean;
          result?: unknown;
          error?: string;
        };
        if (json.error === "method not allowed") {
          return undefined;
        }
        // Cloudflare 502/503 while the origin blips — polls retry; don't unhandled-reject.
        if (r.status === 502 || r.status === 503) {
          return undefined;
        }
        if (!r.ok || json.ok === false) {
          throw new Error(json.error || `HTTP ${r.status}`);
        }
        return json.result;
      })
      .catch((err: unknown) => {
        if (isRemoteNetworkBlip(err)) return undefined;
        throw err;
      })
      .finally(() => {
        if (_inflight.get(key) === pending) _inflight.delete(key);
      });
    _inflight.set(key, pending);
    return pending;
  };
  return new Proxy({} as PanelApi, {
    get(_target, prop) {
      if (typeof prop !== "string" || prop === "then") return undefined;
      return (...args: unknown[]) => invoke(prop, args);
    },
  });
}

let _directApi: PanelApi | null = null;

/** PanelApi over the direct WebRTC rpc channel (phone → PC, no tunnel). */
function directApi(): PanelApi {
  if (_directApi) return _directApi;
  _directApi = new Proxy({} as PanelApi, {
    get(_target, prop) {
      if (typeof prop !== "string" || prop === "then") return undefined;
      return (...args: unknown[]) => {
        if (prop === "open_external_url") {
          openHttpsOnThisDevice(String(args[0] || ""));
          return Promise.resolve();
        }
        if (prop === "copy_text") {
          return navigator.clipboard.writeText(String(args[0] ?? "")).then(() => true, () => false);
        }
        const transport = getDirectTransport();
        if (!transport) return Promise.resolve(undefined);
        const key = remoteInflightKey(prop, args);
        const hit = _inflight.get(key);
        if (hit) return hit;
        const pending = transport
          .invoke(prop, args)
          .catch((err: unknown) => {
            const msg = String((err as Error)?.message || err);
            if (msg.startsWith("method not allowed") || msg === "desktop not connected" || msg === "timeout") return undefined;
            throw err;
          })
          .finally(() => {
            if (_inflight.get(key) === pending) _inflight.delete(key);
          });
        _inflight.set(key, pending);
        return pending;
      };
    },
  });
  return _directApi;
}

/**
 * Methods whose replies run to hundreds of KB. Over the bridge each comes back as one
 * injected script, copied several times and parsed on the window thread (ui_js 270 MB
 * in 2 h); in the desktop window they go over `/__panel_api` instead, bridge as fallback.
 */
export const HTTP_ROUTED_METHODS: ReadonlySet<string> = new Set([
  "get_skill_info",
  "list_project_file_paths",
  "get_uefn_plugin_contributions",
  "list_all_conversations",
  "list_coding_agents",
  "workflow_run_snapshot",
  "list_agent_profiles",
  "get_plan",
  "list_changesets",
  "list_workflow_nodes",
  "list_uefn_plugins",
  "load_messages",
  "load_tool_result",
]);

const _routed = new WeakMap<PanelApi, PanelApi>();

/** `api` with HTTP_ROUTED_METHODS sent over HTTP; one wrapper per bridge object. */
function routedApi(api: PanelApi): PanelApi {
  const hit = _routed.get(api);
  if (hit) return hit;
  const wrapped = new Proxy(api, {
    get(target, prop) {
      const value = Reflect.get(target, prop, target) as unknown;
      if (typeof prop !== "string" || typeof value !== "function" || !HTTP_ROUTED_METHODS.has(prop)) return value;
      const bridge = value as (...args: unknown[]) => Promise<unknown>;
      return async (...args: unknown[]) => {
        if (canCallOverHttp()) {
          const http = await callOverHttp(prop, args);
          if (http.ok) return http.result;
        }
        return bridge.apply(target, args);
      };
    },
  });
  _routed.set(api, wrapped);
  return wrapped;
}

export function getApi(): PanelApi | null {
  const w = typeof window !== "undefined" ? window : undefined;
  const api = w?.pywebview?.api;
  if (isPanelApiReady(api)) return routedApi(api);
  if (!w?.pywebview) {
    // No pywebview: a browser (tunnel host, panel host, or a node test).
    if (getDirectTransport()) return directApi();
    return remoteApi();
  }
  return null;
}

export function requireApi(): PanelApi {
  const api = getApi();
  if (!api) {
    throw new Error("UEFN Ducky panel API unavailable (run inside UEFN-Ducky.exe)");
  }
  return api;
}
