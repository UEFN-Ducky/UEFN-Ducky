import type { PanelApi } from "../types/panel";

/** True when the panel is running in a normal browser (no pywebview). */
export function isRemote(): boolean {
  return typeof window !== "undefined" && !window.pywebview;
}

/** pywebview may set `window.pywebview.api` before methods are bound — probe a core method. */
export function isPanelApiReady(api: unknown): api is PanelApi {
  return !!api && typeof (api as PanelApi).get_listener_status === "function";
}

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
    return fetch("/__panel_api/" + encodeURIComponent(name), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ args }),
    }).then(async (r) => {
      const json = (await r.json().catch(() => ({}))) as {
        ok?: boolean;
        result?: unknown;
        error?: string;
      };
      if (!r.ok || json.ok === false) {
        throw new Error(json.error || `HTTP ${r.status}`);
      }
      return json.result;
    });
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
