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
}

type DropdownCoords = {
  left: number;
  top?: number;
  bottom?: number;
  panelW: number;
  maxHeight: number;
  transformOrigin: string;
};

const EDGE = 8;
const GAP = 8;
const IDEAL_H = 360;

export function DropdownPanel({
  anchorRef,
  open,
  onClose,
  children,
  placement = "bottom",
  minWidth = 200,
  width,
  zIndex = 100010,
}: DropdownPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const scopeClass = useScopedClass("dropdown-panel");
  const [coords, setCoords] = useState<DropdownCoords | null>(null);

  useLayoutEffect(() => {
    if (!open || !anchorRef.current) {
      setCoords(null);
      return;
    }
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
      const openDown =
        preferTop
          ? spaceAbove < 160 && spaceBelow > spaceAbove
          : spaceBelow >= Math.min(IDEAL_H, spaceAbove) || spaceBelow >= spaceAbove;
      const maxHeight = Math.max(120, Math.min(IDEAL_H, openDown ? spaceBelow : spaceAbove));
      const next: DropdownCoords = openDown
        ? {
            left,
            top: rect.bottom + GAP,
            panelW,
            maxHeight,
            transformOrigin: "top left",
          }
        : {
            left,
            bottom: window.innerHeight - rect.top + GAP,
            panelW,
            maxHeight,
            transformOrigin: "bottom left",
          };
      setCoords(next);
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, anchorRef, placement, minWidth, width]);

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
      onClose();
    };
    document.addEventListener("pointerdown", handlePointerDown, true);
    return () => document.removeEventListener("pointerdown", handlePointerDown, true);
  }, [open, onClose, anchorRef]);

  if (!open) return null;

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
            "z-index": String(zIndex),
          }}
        />
      ) : null}
      <div ref={panelRef} className={`dropdown-panel no-drag is-positioned ${scopeClass}`}>
        {children}
      </div>
    </>,
    document.body,
  );
}
