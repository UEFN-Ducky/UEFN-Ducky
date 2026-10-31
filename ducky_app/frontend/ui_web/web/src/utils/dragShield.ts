/**
 * While a tab or a sidebar row is dragged, plugin panels must not take the drag.
 *
 * A plugin panel is an iframe, so its own page got the drag events, and the Browser's page
 * is a native window drawn above the app: the pane under either never saw the drag, so it
 * showed no split squares and the drop did nothing (Oct 31 2026). Shielded, iframes let the
 * pointer through and native pages hide until the drag ends. Every plugin tab splits this
 * way; no plugin has to ask for it.
 */

const reasons = new Set<string>();

function apply(on: boolean): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("ducky-drag-shield", on);
  window.__duckySetBrowserUiCover?.(on);
}

/** A drag whose source unmounts mid-drag (its tab moved away) never lowers its reason. */
function lowerAll(): void {
  if (reasons.size === 0) return;
  reasons.clear();
  apply(false);
  window.removeEventListener("dragend", lowerAll, true);
  window.removeEventListener("drop", lowerAll, true);
}

export function raiseDragShield(reason: string): void {
  if (reasons.has(reason)) return;
  reasons.add(reason);
  if (reasons.size > 1) return;
  apply(true);
  if (typeof window === "undefined") return;
  window.addEventListener("dragend", lowerAll, true);
  window.addEventListener("drop", lowerAll, true);
}

export function lowerDragShield(reason: string): void {
  if (!reasons.delete(reason) || reasons.size > 0) return;
  apply(false);
  if (typeof window === "undefined") return;
  window.removeEventListener("dragend", lowerAll, true);
  window.removeEventListener("drop", lowerAll, true);
}

export function dragShieldRaised(): boolean {
  return reasons.size > 0;
}
