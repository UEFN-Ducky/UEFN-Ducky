const KEY = "uefn-history-hover-preview";

/** Whether hovering a history entry auto-previews it in the editor. Default: on. */
export function getHistoryHoverPreview(): boolean {
  try {
    // Default true — only an explicit opt-out disables hover preview.
    return localStorage.getItem(KEY) !== "false";
  } catch {
    return true;
  }
}

export function setHistoryHoverPreview(value: boolean): void {
  try {
    localStorage.setItem(KEY, value ? "true" : "false");
  } catch {
    /* ignore storage errors */
  }
}
