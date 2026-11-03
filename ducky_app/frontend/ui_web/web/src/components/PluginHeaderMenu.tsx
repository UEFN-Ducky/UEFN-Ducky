import { useMemo, useRef, useState } from "react";
import { PluginWebviewPane } from "../plugin-ui";
import type { HeaderMenuBadge, HeaderMenuHost } from "../plugin-ui/bridge";
import { pluginUiTabId } from "../plugin-ui/types";
import { resolvePluginHeaderIcon } from "../hooks/pluginHeaderActions";
import type { PluginHeaderMenu } from "../hooks/usePluginContributions";
import { DropdownPanel } from "./DropdownPanel";

/**
 * One `contributes.header.menus` entry: a title-bar button whose dropdown is the plugin's own
 * page (`ui: "panel:<id>"`). The page stays loaded while the menu is closed, so it can keep the
 * button's dot and count current (bridge `header_menu_badge`) and close the menu
 * (`header_menu_close`) after an item that opens a tab.
 */
/** The page's reported height, kept inside 120 px and the window (past that the page scrolls). */
export function headerMenuHeight(height: number, viewport: number = typeof window === "undefined" ? 800 : window.innerHeight): number {
  const max = Math.max(120, Math.min(640, viewport - 96));
  return Math.round(Math.min(max, Math.max(120, height)));
}

export function PluginMenuButton({ menu }: { menu: PluginHeaderMenu }) {
  const [open, setOpen] = useState(false);
  const [badge, setBadge] = useState<HeaderMenuBadge>({ dot: "", count: 0 });
  const [height, setHeight] = useState(240);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const pluginId = String(menu.plugin_id || "").trim().toLowerCase();
  const panelId = menu.ui.slice("panel:".length).trim().toLowerCase();
  // Its own tab id, so the menu's page never shares a pane with the same panel opened as a tab.
  const tabId = `${pluginUiTabId(pluginId, panelId)}~header`;
  const host = useMemo<HeaderMenuHost>(
    () => ({ badge: setBadge, close: () => setOpen(false), size: (h) => setHeight(headerMenuHeight(h)) }),
    [],
  );
  const title = menu.title || menu.id;

  return (
    <div className="connection-status-root no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`no-drag connection-status-btn app-header-team-btn${open ? " is-active" : ""}`}
        title={title}
        aria-label={title}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        {resolvePluginHeaderIcon(menu.icon)}
        {badge.dot ? (
          <span className={`plugin-scope-dot plugin-scope-dot--${badge.dot} app-header-team-btn__dot`} data-dot={badge.dot} aria-hidden />
        ) : null}
        {badge.count ? (
          <span className={`plugin-scope-count app-header-team-btn__count${badge.dot === "error" ? " is-alert" : ""}`}>
            {badge.count}
          </span>
        ) : null}
      </button>
      {pluginId && panelId ? (
        <DropdownPanel anchorRef={anchorRef} open={open} onClose={() => setOpen(false)}
          width={menu.width || 320} placement="bottom" keepMounted>
          <div className="plugin-header-menu" style={{ height }} data-height={height}>
            <PluginWebviewPane tabId={tabId} visible={open} headerMenu={host} />
          </div>
        </DropdownPanel>
      ) : null}
    </div>
  );
}
