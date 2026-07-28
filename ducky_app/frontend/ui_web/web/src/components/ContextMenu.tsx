import { createPortal } from "react-dom";

import { useEffect, useRef, useState } from "react";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";

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
  onClick?: () => void;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
}

export function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const scopeClass = useScopedClass("context-menu");

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

  const panelW = 220;
  const rowCount = items.filter((item) => !item.separator).length;
  let left = x;
  let top = y;
  if (left + panelW > window.innerWidth - 8) left = Math.max(8, window.innerWidth - panelW - 8);
  if (top + rowCount * 32 > window.innerHeight - 8) top = Math.max(8, top - rowCount * 32);

  return createPortal(
    <>
      <ScopedCss
        selector={`.${scopeClass}`}
        rules={{
          "--context-menu-left": `${left}px`,
          "--context-menu-top": `${top}px`,
          "--context-menu-min-width": `${panelW}px`,
        }}
      />
      <div ref={panelRef} className={`context-menu is-positioned ${scopeClass}`} role="menu">
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
              <span className="context-menu-label">{item.label}</span>
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

  const close = () => setMenu(null);

  return { menu, open, close };
}
