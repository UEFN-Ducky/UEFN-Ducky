import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

type TrapEntry = {
  container: HTMLElement;
  onEscape?: () => void;
};

const trapStack: TrapEntry[] = [];

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hidden && el.getAttribute("aria-hidden") !== "true",
  );
}

function isTopTrap(entry: TrapEntry): boolean {
  return trapStack[trapStack.length - 1] === entry;
}

export interface FocusTrapOptions {
  onEscape?: () => void;
}

export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  options?: FocusTrapOptions,
) {
  const onEscapeRef = useRef(options?.onEscape);
  onEscapeRef.current = options?.onEscape;

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    const entry: TrapEntry = {
      container,
      onEscape: () => onEscapeRef.current?.(),
    };
    trapStack.push(entry);

    const previousFocus = document.activeElement as HTMLElement | null;

    const focusables = getFocusableElements(container);
    if (focusables.length > 0) {
      focusables[0].focus();
    } else {
      container.setAttribute("tabindex", "-1");
      container.focus();
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (!isTopTrap(entry)) return;

      if (e.key === "Escape") {
        if (onEscapeRef.current) {
          e.preventDefault();
          e.stopPropagation();
          onEscapeRef.current();
        }
        return;
      }

      if (e.key !== "Tab") return;

      const focusable = getFocusableElements(container);
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeEl = document.activeElement as HTMLElement;

      if (!container.contains(activeEl)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
        return;
      }

      if (e.shiftKey) {
        if (activeEl === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (activeEl === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = trapStack.indexOf(entry);
      if (index !== -1) trapStack.splice(index, 1);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [active, containerRef]);
}
