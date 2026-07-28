import type { PanelApi } from "../types/panel";

/** pywebview may set `window.pywebview.api` before methods are bound — probe a core method. */
export function isPanelApiReady(api: unknown): api is PanelApi {
  return !!api && typeof (api as PanelApi).get_listener_status === "function";
}

export function getApi(): PanelApi | null {
  const api = window.pywebview?.api;
  return isPanelApiReady(api) ? api : null;
}

export function requireApi(): PanelApi {
  const api = getApi();
  if (!api) {
    throw new Error("UEFN Ducky panel API unavailable (run inside UEFN-Ducky.exe)");
  }
  return api;
}
