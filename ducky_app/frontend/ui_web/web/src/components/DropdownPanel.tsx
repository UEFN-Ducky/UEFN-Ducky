import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";

type Placement = "bottom" | "top";

interface DropdownPanelProps {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  placement?: Placement;
  minWidth?: number;
  width?: number;
  /** Above modal overlays (100001). Default matches global dropdown layer. */
  zIndex?: number;
  /** Children own scrolling (model list). Without this the panel and the list both scroll. */
  clip?: boolean;
  /** Max panel height in px. Capped by leftover viewport space. Short lists shrink. */
  idealHeight?: number;
  /** Pin footers and let the list scroll when content hits the cap. */
  fill?: boolean;
}

type DropdownCoords = {
  left: number;
  top?: number;
  bottom?: number;
  panelW: number;
  maxHeight: number;
  transformOrigin: string;
};

/** Open panels; while any is open, native caption surfaces hand their clicks to the DOM. */
let openPanels = 0;
export const DROPDOWN_OPEN_CLASS = "dropdown-open";

const EDGE = 8;
const GAP = 8;
const IDEAL_H = 360;
/** How long a menu takes to zoom back into its icon (matches `.menu-pop.is-closing`). */
export const MENU_POP_MS = 150;

/** Keeps a menu mounted after it closes, long enough to zoom back into the icon that opened it. */
export function useMenuPop(open: boolean): { mounted: boolean; closing: boolean } {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    // Nothing to watch where nothing animates (reduced motion, or a DOM without animations like jsdom).
    const still =
      !("getAnimations" in Element.prototype) || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (still) {
      setMounted(false);
      return;
    }
    const timer = window.setTimeout(() => setMounted(false), MENU_POP_MS);
    return () => window.clearTimeout(timer);
  }, [open]);
  return { mounted: open || mounted, closing: !open && mounted };
}

/** transform-origin on the trigger's center, for a menu placed at `pos`, so the menu zooms out of its icon. */
export function popOrigin(
  trigger: Element | null,
  pos: { left: number; top?: number; bottom?: number },
): string | undefined {
  if (!trigger) return undefined;
  const rect = trigger.getBoundingClientRect();
  const x = rect.left + rect.width / 2 - pos.left;
  const y = rect.top + rect.height / 2;
  if (pos.top !== undefined) return `${x}px ${y - pos.top}px`;
  return `${x}px calc(100% + ${y - (window.innerHeight - (pos.bottom ?? 0))}px)`;
}

/**
 * Closes a menu on a press outside it and its trigger, on Escape, and when the window loses focus
 * (a click inside a plugin iframe or on a window-drag area never reaches this document).
 * A handler that calls preventDefault on the Escape keydown keeps the menu open.
 */
export function useDropdownDismiss(
  open: boolean,
  onClose: () => void,
  panelRef: RefObject<HTMLElement | null>,
  anchorRef: RefObject<HTMLElement | null>,
) {
  // A click on an `-webkit-app-region: drag` surface is taken by Windows and never reaches the
  // page; global.css turns those surfaces off while this class is on (WindowDrag still moves the window).
  useEffect(() => {
    if (!open) return;
    openPanels += 1;
    document.body.classList.add(DROPDOWN_OPEN_CLASS);
    return () => {
      openPanels = Math.max(0, openPanels - 1);
      if (!openPanels) document.body.classList.remove(DROPDOWN_OPEN_CLASS);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      // A modal layered above this dropdown owns its own dismissal — interacting
      // with it (or clicking its backdrop to close it) must not also collapse the
      // dropdown underneath. Close them one at a time.
      if (target instanceof Element && target.closest(".modal-backdrop")) return;
      // Nested portaled menus (language picker, etc.) live outside this panel.
      if (target instanceof Element && target.closest("[data-dropdown-keep]")) return;
      onClose();
    };
    const handleBlur = () => {
      const active = document.activeElement;
      if (active && panelRef.current?.contains(active)) return;
      if (active instanceof Element && active.closest("[data-dropdown-keep]")) return;
      onClose();
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
    };
    document.addEventListener("pointerdown", handlePointerDown, true);
    window.addEventListener("blur", handleBlur);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      window.removeEventListener("blur", handleBlur);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose, panelRef, anchorRef]);
}

export function DropdownPanel({
  anchorRef,
  open,
  onClose,
  children,
  placement = "bottom",
  minWidth = 200,
  width,
  zIndex = 100010,
  clip = false,
  idealHeight = IDEAL_H,
  fill = false,
}: DropdownPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const scopeClass = useScopedClass("dropdown-panel");
  const [coords, setCoords] = useState<DropdownCoords | null>(null);
  const pop = useMenuPop(open);

  // Closing keeps the last coords so the panel zooms back into its anchor where it stood.
  useLayoutEffect(() => {
    if (!open || !anchorRef.current) return;
    const update = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const panelW = width ?? Math.max(minWidth, rect.width);
      let left = rect.left;
      if (left + panelW > window.innerWidth - EDGE) {
        left = Math.max(EDGE, window.innerWidth - panelW - EDGE);
      }
      const spaceBelow = Math.max(0, window.innerHeight - rect.bottom - GAP - EDGE);
      const spaceAbove = Math.max(0, rect.top - GAP - EDGE);
      // Honor preferred placement when it fits; otherwise flip to the roomier side.
      const preferTop = placement === "top";
      const want = Math.max(120, idealHeight);
      const openDown =
        preferTop
          ? spaceAbove < 160 && spaceBelow > spaceAbove
          : spaceBelow >= Math.min(want, spaceAbove) || spaceBelow >= spaceAbove;
      const maxHeight = Math.max(120, Math.min(want, openDown ? spaceBelow : spaceAbove));
      const top = rect.bottom + GAP;
      const bottom = window.innerHeight - rect.top + GAP;
      const next: DropdownCoords = openDown
        ? { left, top, panelW, maxHeight, transformOrigin: popOrigin(anchor, { left, top }) ?? "top left" }
        : { left, bottom, panelW, maxHeight, transformOrigin: popOrigin(anchor, { left, bottom }) ?? "bottom left" };
      setCoords(next);
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, anchorRef, placement, minWidth, width, idealHeight]);

  useDropdownDismiss(open, onClose, panelRef, anchorRef);

  if (!pop.mounted) return null;

  return createPortal(
    <>
      {coords ? (
        <ScopedCss
          selector={`.${scopeClass}`}
          rules={{
            "--dropdown-left": `${coords.left}px`,
            ...(coords.top !== undefined ? { "--dropdown-top": `${coords.top}px` } : { "--dropdown-top": "auto" }),
            ...(coords.bottom !== undefined
              ? { "--dropdown-bottom": `${coords.bottom}px` }
              : { "--dropdown-bottom": "auto" }),
            "--dropdown-width": `${coords.panelW}px`,
            "--dropdown-max-height": `${coords.maxHeight}px`,
            "--dropdown-transform-origin": coords.transformOrigin,
            "--dropdown-z": String(zIndex),
          }}
        />
      ) : null}
      <div
        ref={panelRef}
        className={`dropdown-panel no-drag is-positioned menu-pop${pop.closing ? " is-closing" : ""} ${scopeClass}${clip ? " dropdown-panel--clip" : ""}${fill ? " dropdown-panel--fill" : ""}`}
      >
        {children}
      </div>
    </>,
    document.body,
  );
}
