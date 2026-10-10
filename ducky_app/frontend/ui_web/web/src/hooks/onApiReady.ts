import { getApi } from "./usePanelApi";

/**
 * The desktop window before pywebview has injected its bridge. pywebview only does that
 * once the page has finished loading, long after the app's first effects ran; until then
 * getApi() answers over HTTP. A phone or tunnel browser has no `window.chrome.webview`.
 */
function awaitingDesktopBridge(): boolean {
  if (typeof window === "undefined" || window.pywebview) return false;
  return !!(window as Window & { chrome?: { webview?: unknown } }).chrome?.webview;
}

/**
 * Run callback once when pywebview API is available (immediate or on pywebviewready).
 *
 * In the desktop window this waits for pywebview even though getApi() already answers
 * over HTTP: callers keep the API they are handed (the listener and project polls), and
 * the HTTP one would stay in use for the life of the page.
 */
export function onApiReady(cb: (api: NonNullable<ReturnType<typeof getApi>>) => void): () => void {
  const readyApi = () => (awaitingDesktopBridge() ? null : getApi());
  const existing = readyApi();
  if (existing) {
    cb(existing);
    return () => {};
  }

  let done = false;
  let intervalId: number | undefined;

  const stop = () => {
    window.removeEventListener("pywebviewready", tryReady);
    if (intervalId !== undefined) {
      window.clearInterval(intervalId);
      intervalId = undefined;
    }
  };

  const fire = (api: NonNullable<ReturnType<typeof getApi>>) => {
    if (done) return;
    done = true;
    stop();
    cb(api);
  };

  const tryReady = () => {
    const api = readyApi();
    if (api) fire(api);
  };

  window.addEventListener("pywebviewready", tryReady);
  intervalId = window.setInterval(tryReady, 100);

  return stop;
}
