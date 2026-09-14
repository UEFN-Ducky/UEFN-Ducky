/** Narrow/mobile overlay drawers — session only, closed on every overlay entry. */

export const OVERLAY_RAIL_SESSION_EVENT = "uefn-overlay-rail-session";

export type OverlayRailSession = { left: boolean; right: boolean };

let session: OverlayRailSession = { left: false, right: false };

export function effectiveRailOpen(opts: {
  overlay: boolean;
  persisted: boolean;
  session: boolean;
}): boolean {
  return opts.overlay ? opts.session : opts.persisted;
}

export function readOverlayRailSession(): OverlayRailSession {
  return session;
}

function notify(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(OVERLAY_RAIL_SESSION_EVENT));
}

export function resetOverlayRailSession(): void {
  if (!session.left && !session.right) return;
  session = { left: false, right: false };
  notify();
}

/** Open one overlay drawer; opening one closes the other. */
export function setOverlayRailOpen(side: "left" | "right", open: boolean): void {
  const next: OverlayRailSession =
    side === "left"
      ? { left: open, right: open ? false : session.right }
      : { left: open ? false : session.left, right: open };
  if (next.left === session.left && next.right === session.right) return;
  session = next;
  notify();
}

export function toggleOverlayRail(side: "left" | "right"): void {
  setOverlayRailOpen(side, side === "left" ? !session.left : !session.right);
}

export function subscribeOverlayRailSession(onChange: () => void): () => void {
  window.addEventListener(OVERLAY_RAIL_SESSION_EVENT, onChange);
  return () => window.removeEventListener(OVERLAY_RAIL_SESSION_EVENT, onChange);
}
