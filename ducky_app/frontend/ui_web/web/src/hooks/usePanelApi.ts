import type { PanelApi } from "../types/panel";

/** True when the panel is running in a normal browser (no pywebview). */
export function isRemote(): boolean {
  return typeof window !== "undefined" && !window.pywebview;
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
      const url = String(args[0] || "");
      if (url) window.open(url, "_blank", "noopener,noreferrer");
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

export function getApi(): PanelApi | null {
  const api = window.pywebview?.api;
  if (isPanelApiReady(api)) return api;
  if (typeof window !== "undefined" && !window.pywebview) return remoteApi();
  return null;
}

export function requireApi(): PanelApi {
  const api = getApi();
  if (!api) {
    throw new Error("UEFN Ducky panel API unavailable (run inside UEFN-Ducky.exe)");
  }
  return api;
}
