/** Longest a routed list call waits over HTTP before giving up (same as a chat load). */
export const PANEL_HTTP_TIMEOUT_MS = 45_000;

export type HttpCall = { ok: true; result: unknown } | { ok: false; timedOut: boolean };

/**
 * True in the desktop window when its page is served by the local server that also
 * answers `/__panel_api`: a big reply then comes back as a plain fetch body instead of
 * one injected script that pywebview copies and parses on the window thread.
 */
export function canCallOverHttp(): boolean {
  return typeof window !== "undefined" && !!window.pywebview && window.location.protocol.startsWith("http");
}

/**
 * One PanelApi call over `/__panel_api`; `{ok: false}` on any failure so the caller can use the bridge.
 * `requestId`: the server runs the call once per id, so a retry after a timeout never repeats it.
 */
export async function callOverHttp(
  method: string,
  args: unknown[],
  timeoutMs = PANEL_HTTP_TIMEOUT_MS,
  requestId = "",
): Promise<HttpCall> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch("/__panel_api/" + encodeURIComponent(method), {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...(requestId ? { "X-Ducky-Request-Id": requestId } : {}),
      },
      body: JSON.stringify({ args }),
      signal: controller.signal,
    });
    if (res.ok) {
      const json = (await res.json()) as { ok?: boolean; result?: unknown };
      if (json.ok !== false) return { ok: true, result: json.result };
    }
  } catch {
    // Network error, bad JSON or timeout: the caller falls back.
  } finally {
    window.clearTimeout(timer);
  }
  return { ok: false, timedOut: controller.signal.aborted };
}

/**
 * A send carrying pictures (MBs of base64 each) goes over HTTP: through the pywebview bridge
 * a few screenshots never came back and the chat sat on "Thinking" with nothing saved (Nov 7
 * 2026). A timeout retries once under the same id; it never falls back to the bridge then,
 * which could send it twice. `{ok: false, timedOut: false}`: HTTP refused it, use the bridge.
 */
export async function sendWithPicturesOverHttp(args: unknown[]): Promise<HttpCall> {
  const requestId = `send-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  let call = await callOverHttp("send_message", args, PANEL_HTTP_TIMEOUT_MS, requestId);
  if (!call.ok && call.timedOut) call = await callOverHttp("send_message", args, PANEL_HTTP_TIMEOUT_MS, requestId);
  return call;
}
