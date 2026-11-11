import { useLayoutEffect, useRef, useState } from "react";

/** How long the body stays mounted after closing, so its height can animate shut
 *  (`.chat-plan-popup-reveal` in chat.css uses the same time). */
export const DOCK_REVEAL_MS = 260;

/**
 * A bar docked above the composer that opens into one card growing upward: the plan bar
 * and the workflow run bar. Bar and body are one box; only the height animates.
 *
 * `present` keeps the body mounted through the close animation. `expanded` flips a frame
 * after the body mounts, so the height can transition from zero. While the card is open
 * its `--plan-open-max` is the room from the chat pane's top down to the dock, so the bar
 * stays on screen and the body scrolls inside the card.
 */
export function useDockReveal(open: boolean, dockSelector: string) {
  const [present, setPresent] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const popupRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (open) {
      setPresent(true);
      return;
    }
    setExpanded(false);
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const timer = window.setTimeout(() => setPresent(false), reduce ? 0 : DOCK_REVEAL_MS);
    return () => window.clearTimeout(timer);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !present) return;
    const frame = requestAnimationFrame(() => setExpanded(true));
    return () => cancelAnimationFrame(frame);
  }, [open, present]);

  useLayoutEffect(() => {
    const popup = popupRef.current;
    if (!present || !popup) return;
    const dock = popup.closest(dockSelector);
    const pane = popup.closest(".chat-pane-root");
    if (!(dock instanceof HTMLElement) || !(pane instanceof HTMLElement)) return;
    const input = popup.closest(".chat-pane-input-area");
    const fit = () => {
      const room = dock.getBoundingClientRect().bottom - pane.getBoundingClientRect().top - 8;
      popup.style.setProperty("--plan-open-max", `${Math.max(96, Math.floor(room))}px`);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(pane);
    if (input instanceof HTMLElement) ro.observe(input);
    return () => {
      ro.disconnect();
      popup.style.removeProperty("--plan-open-max");
    };
  }, [present, dockSelector]);

  return { popupRef, present, expanded };
}
