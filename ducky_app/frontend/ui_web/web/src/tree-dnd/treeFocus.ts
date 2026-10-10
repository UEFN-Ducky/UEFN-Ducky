/**
 * After a delete or move removes the focused row, focus would fall to the page and
 * Ctrl+Z would no longer reach that tree. Put it back on the tree itself.
 */
export function keepTreeFocus(scope: string): void {
  if (typeof document === "undefined") return;
  const active = document.activeElement;
  if (active && active !== document.body && active.isConnected) return;
  for (const el of document.querySelectorAll<HTMLElement>("[data-undo-scope]")) {
    if (el.getAttribute("data-undo-scope") !== scope) continue;
    if (!el.getClientRects().length && el.offsetParent === null) continue;
    el.focus({ preventScroll: true });
    return;
  }
}
