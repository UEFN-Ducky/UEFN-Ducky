import { createPortal } from "react-dom";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { useMergedRef, useUiTarget } from "../ui-targets/registry";

export interface ContextMenuItem {
  id: string;
  label: string;
  disabled?: boolean;
  danger?: boolean;
  separator?: boolean;
  checked?: boolean;
  /** Green/red slider instead of a checkmark; uses `checked` for on/off. */
  switch?: boolean;
  /** Keep menu open after click (e.g. checkbox toggles). */
  keepOpen?: boolean;
  /** Small icon before the label (the button or thing the row is about). */
  icon?: ReactNode;
  onClick?: () => void;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
  /** Spotlight id while this menu is the Duckies header menu. */
  tourTarget?: string;
  /** Clicking a row leaves focus (and the caret) where it was: a text box's own menu. */
  keepFocus?: boolean;
  /** Zooming back into the point it opened from (a button's menu kept on screen by useMenuPop). */
  closing?: boolean;
  /** The point it zooms out of when not (x, y): its button's center. */
  from?: { x: number; y: number };
}

export function ContextMenu({ x, y, items, onClose, tourTarget, keepFocus, closing, from }: ContextMenuProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const scopeClass = useScopedClass("context-menu");
  const tourRef = useUiTarget(tourTarget || "", {
    kind: "button",
    label: "Duckies menu",
    route: "chat",
  });
  const setPanelRef = useMergedRef(panelRef, tourRef);

  useEffect(() => {
    const handlePointerDown = (e: PointerEvent) => {
      if (panelRef.current?.contains(e.target as Node)) return;
      onClose();
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  // The real size once drawn (switch rows are taller than the guess); the guess places the first frame.
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  // offset sizes: the opening zoom scales the drawn box, never the laid-out one.
  useLayoutEffect(() => {
    const el = panelRef.current;
    if (!el?.offsetHeight) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    setSize((cur) => (cur && cur.w === w && cur.h === h ? cur : { w, h }));
  });

  const panelW = 220;
  const rowCount = items.filter((item) => !item.separator).length;
  // A long list scrolls inside the window instead of running off it.
  const maxHeight = Math.max(120, window.innerHeight - 16);
  const width = size?.w || panelW;
  const height = Math.min(size?.h || rowCount * 32 + 8, maxHeight);
  let left = x;
  let top = y;
  if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
  // No room below the pointer: open upward from it, like a native menu; else slide it in.
  if (top + height > window.innerHeight - 8) top = y - height >= 8 ? y - height : Math.max(8, window.innerHeight - height - 8);
  const withIcons = items.some((item) => item.icon);

  return createPortal(
    <>
      <ScopedCss
        selector={`.${scopeClass}`}
        rules={{
          "--context-menu-left": `${left}px`,
          "--context-menu-top": `${top}px`,
          "--context-menu-min-width": `${panelW}px`,
          "--context-menu-max-height": `${maxHeight}px`,
          // It zooms out of the point it opened from (the pointer, or its button).
          "--context-menu-origin": `${(from?.x ?? x) - left}px ${(from?.y ?? y) - top}px`,
        }}
      />
      <div ref={setPanelRef} className={`context-menu is-positioned menu-pop${closing ? " is-closing" : ""} ${scopeClass}`} role="menu">
        {items.map((item) =>
          item.separator ? (
            <div key={item.id} className="context-menu-separator" role="separator" />
          ) : (
            <button
              key={item.id}
              type="button"
              role={item.checked !== undefined || item.switch ? "menuitemcheckbox" : "menuitem"}
              aria-checked={item.switch || item.checked !== undefined ? Boolean(item.checked) : undefined}
              className={`context-menu-item${item.danger ? " is-danger" : ""}${item.switch ? " is-switch" : item.checked !== undefined ? " is-checkable" : ""}`}
              disabled={item.disabled}
              onMouseDown={keepFocus ? (e) => e.preventDefault() : undefined}
              onClick={() => {
                if (item.disabled) return;
                item.onClick?.();
                if (!item.keepOpen) onClose();
              }}
            >
              {item.switch ? null : (
                <span className="context-menu-check" aria-hidden="true">
                  {item.checked ? "✓" : ""}
                </span>
              )}
              {withIcons ? (
                <span className="context-menu-icon" aria-hidden="true">
                  {item.icon}
                </span>
              ) : null}
              <span className="context-menu-label" title={item.label}>
                {item.label}
              </span>
              {item.switch ? (
                <span
                  className={`context-menu-switch${item.checked ? " is-on" : ""}`}
                  aria-hidden="true"
                >
                  <span className="context-menu-switch-knob" />
                </span>
              ) : null}
            </button>
          ),
        )}
      </div>
    </>,
    document.body,
  );
}

export function useContextMenuState<T>() {
  const [menu, setMenu] = useState<{ x: number; y: number; data: T } | null>(null);

  const open = (e: React.MouseEvent, data: T) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu({ x: e.clientX, y: e.clientY, data });
  };

  const openAt = useCallback((x: number, y: number, data: T) => {
    setMenu({ x, y, data });
  }, []);

  const close = () => setMenu(null);

  return { menu, open, openAt, close };
}
