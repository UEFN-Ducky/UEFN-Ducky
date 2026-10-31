import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMenuPop } from "../components/DropdownPanel";

/** checked: a radio (one of several); toggle: an on/off switch that keeps the menu open. */
export type CanvasMenuItem = { value: string; label: string; hint?: string; checked?: boolean; toggle?: boolean; separatorBefore?: boolean; heading?: string };

/** A menu that opens above a bottom-toolbar button in the toolbar's own see-through,
 *  blurred look (not the app's generic dropdown). It stays open while you pick (zoom in a
 *  few times, switch the grid, toggle snapping); Esc or a click elsewhere closes it. */
export function CanvasMenu({ label, title, trigger, items, onPick, className = "", buttonRef }: {
  label: string;
  title?: string;
  trigger: ReactNode;
  items: CanvasMenuItem[];
  onPick: (value: string) => void;
  className?: string;
  buttonRef?: (el: HTMLButtonElement | null) => void;
}) {
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement>(null);
  // Closing keeps the last spot (and the menu, while it zooms back into its button).
  const [at, setAt] = useState<{ left: number; bottom: number; origin: string } | null>(null);
  const [open, setOpen] = useState(false);
  const pop = useMenuPop(open);
  const close = () => setOpen(false);
  const toggle = () => {
    if (open || !button.current) { close(); return; }
    const rect = button.current.getBoundingClientRect();
    setAt({ left: rect.left + rect.width / 2, bottom: window.innerHeight - rect.top + 10,
      origin: `50% calc(100% + ${10 + rect.height / 2}px)` });
    setOpen(true);
  };
  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus({ preventScroll: true });
    const away = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !button.current?.contains(target)) close();
    };
    const keys = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close();
      button.current?.focus({ preventScroll: true });
    };
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", keys, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", keys, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  const onArrows = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const all = [...(menu.current?.querySelectorAll<HTMLElement>("button") || [])];
    const at = all.indexOf(document.activeElement as HTMLElement);
    all[(at + (event.key === "ArrowDown" ? 1 : -1) + all.length) % all.length]?.focus({ preventScroll: true });
  };
  return <>
    <button ref={(el) => { button.current = el; buttonRef?.(el); }} type="button" className={className} aria-label={label} title={title} aria-haspopup="menu" aria-expanded={open} onClick={toggle}>{trigger}</button>
    {pop.mounted && at ? createPortal(
      <div ref={menu} className={`aw-canvas-menu${pop.closing ? " is-closing" : ""}`} role="menu" aria-label={label}
        style={{ left: at.left, bottom: at.bottom, transformOrigin: at.origin }} onKeyDown={onArrows}>
        {items.map((item) => <Fragment key={item.value}>
          {item.separatorBefore ? <div className="aw-canvas-menu-sep" role="separator" /> : null}
          {item.heading ? <div className="aw-canvas-menu-heading" role="presentation">{item.heading}</div> : null}
          {item.toggle ? (
            <button type="button" role="menuitemcheckbox" aria-checked={!!item.checked} aria-label={item.label} className="aw-canvas-menu-toggle" onClick={() => onPick(item.value)}>
              <span>{item.label}</span><span className={`aw-switch${item.checked ? " is-on" : ""}`} aria-hidden="true"><span /></span>
            </button>
          ) : (
            <button type="button" role={item.checked === undefined ? "menuitem" : "menuitemradio"} aria-checked={item.checked} aria-label={item.label}
              className={item.checked ? "is-checked" : undefined} onClick={() => onPick(item.value)}>
              <span>{item.label}</span>{item.hint ? <kbd>{item.hint}</kbd> : null}
            </button>
          )}
        </Fragment>)}
      </div>, document.body) : null}
  </>;
}
