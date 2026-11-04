import { getApi } from "../hooks/usePanelApi";
import { WINDOW_ID } from "../tabs/tabRegistryClient";

/** Newer than the typed panel API: the app holds output for terminals no window shows. */
type TerminalViewApi = {
  set_visible_terminals?: (ids: string[], viewer_id: string) => Promise<unknown>;
};
const viewApi = () => getApi() as unknown as TerminalViewApi | null;

const shown = new Set<string>();
let lastKey: string | null = null;
let scheduled = false;
let listening = false;

function send(): void {
  scheduled = false;
  const ids = typeof document !== "undefined" && document.hidden ? [] : [...shown].sort();
  const key = ids.join("\0");
  if (key === lastKey) return;
  lastKey = key;
  void viewApi()?.set_visible_terminals?.(ids, WINDOW_ID);
}

function schedule(): void {
  if (!listening && typeof document !== "undefined") {
    listening = true;
    document.addEventListener("visibilitychange", send);
  }
  if (scheduled) return;
  scheduled = true;
  // One report per change burst: a tab switch hides one pane and shows another.
  queueMicrotask(send);
}

/**
 * A terminal pane in this window is on screen (or not, or gone). Sends only when the set
 * changes; a hidden or minimised window shows none. Output to terminals no window shows
 * is held by the app and replayed when one is shown.
 */
export function setTerminalShown(sessionId: string, isShown: boolean): void {
  if (!sessionId) return;
  const had = shown.has(sessionId);
  if (isShown === had) return;
  if (isShown) shown.add(sessionId);
  else shown.delete(sessionId);
  schedule();
}

export function _resetVisibleTerminalsForTests(): void {
  shown.clear();
  lastKey = null;
  scheduled = false;
}
