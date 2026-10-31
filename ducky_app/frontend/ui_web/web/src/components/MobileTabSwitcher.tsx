import { useSyncExternalStore } from "react";
import { EditorTabGlyph } from "./EditorTabs";
import { Icons } from "../icons/Icons";
import type { EditorTab } from "../types/panel";

/* Phones: the editor's tab row is the Tabs tab of the Ducky menu instead (more room for the
   page). The menu owns the spot while it is open; the focused editor group portals its tabs
   into it, and a pick closes the menu. */
let slot: HTMLElement | null = null;
let onPicked: (() => void) | null = null;
const listeners = new Set<() => void>();

export function setHeaderTabsSlot(el: HTMLElement | null, picked?: () => void) {
  onPicked = el ? picked ?? null : null;
  if (slot === el) return;
  slot = el;
  listeners.forEach((listener) => listener());
}

export function useHeaderTabsSlot() {
  return useSyncExternalStore(
    (onChange) => {
      listeners.add(onChange);
      return () => listeners.delete(onChange);
    },
    () => slot,
    () => null,
  );
}

export function MobileTabSwitcher({ tabs, activeTabId, onActivate, onClose }: {
  tabs: EditorTab[];
  activeTabId: string | null;
  onActivate: (tabId: string) => void;
  onClose: (tabId: string) => void;
}) {
  const active = tabs.find((tab) => tab.id === activeTabId) || tabs[0];
  if (!active) return null;
  return (
    <div className="mobile-tabs-menu" role="menu" aria-label="Open tabs">
      {tabs.map((tab) => (
        <div key={tab.id} className={`mobile-tabs-row${tab.id === active.id ? " is-active" : ""}`}>
          <button type="button" role="menuitem" className="mobile-tabs-pick" aria-current={tab.id === active.id ? "true" : undefined}
            onClick={() => { onActivate(tab.id); onPicked?.(); }}>
            <span className="editor-tab-icon" aria-hidden="true"><EditorTabGlyph tab={tab} /></span>
            <span className="mobile-tabs-name">{tab.name}</span>
          </button>
          <button type="button" className="icon-btn mobile-tabs-close" aria-label={`Close ${tab.name}`} title="Close"
            onClick={() => onClose(tab.id)}>
            <Icons.Close />
          </button>
        </div>
      ))}
    </div>
  );
}
