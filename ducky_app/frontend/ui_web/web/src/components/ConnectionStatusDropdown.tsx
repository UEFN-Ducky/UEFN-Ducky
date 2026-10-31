import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { ConnectionStatusIcon } from "./ConnectionStatusIcon";
import { popOrigin, useDropdownDismiss, useMenuPop } from "./DropdownPanel";
import { DuckiesRows, badgeText, onlineCount, useAccountDuckies } from "./DuckiesDropdown";
import { Icons } from "../icons/Icons";
import type { ListenerStatus } from "../types/panel";
import { useListenerUptimeSec } from "../hooks/useListenerStatus";
import { getApi } from "../hooks/usePanelApi";
import { setHeaderTabsSlot } from "./MobileTabSwitcher";

function isFramedWebView(): boolean {
  return typeof window !== "undefined" && !window.pywebview && window.parent !== window;
}

const MENU_WIDTH = 300;
const MENU_GAP = 6;

function computeMenuPosition(trigger: HTMLElement): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  let left = rect.left;
  if (left + MENU_WIDTH > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - MENU_WIDTH - 8);
  }
  if (left < 8) left = 8;
  return { top: rect.bottom + MENU_GAP, left };
}

interface RowProps {
  label: string;
  detail: string;
  ok: boolean;
  warn?: boolean;
}

/** UEFN and Unreal Editor 5.8+ serve the same Epic MCP on one port; epic_mcp_editor says which holds it. */
export function epicMcpRows(status: ListenerStatus): [RowProps, RowProps] {
  const url = status.epic_mcp_url || "http://127.0.0.1:8000/mcp";
  const reason = String(status.epic_mcp_reason || "").trim();
  const editor = status.epic_mcp_editor || "";
  // other_editor: the editor on the port is not the one this project belongs in.
  const wrongProject = reason === "other_editor";
  const row = (label: string, mine: boolean, otherEditor: string, offline: string): RowProps => {
    if (reason === "disabled") return { label, detail: "Disabled in Settings → MCPs", ok: false };
    if (reason === "port_taken") return { label, detail: "Port taken by another program", ok: false, warn: true };
    if (mine) {
      if (wrongProject) return { label, detail: "Running · not used for this project", ok: false, warn: true };
      if (status.epic_mcp_note) return { label, detail: `Connected · ${status.epic_mcp_note}`, ok: true, warn: true };
      return { label, detail: `Connected · ${url}`, ok: true };
    }
    if (editor) return { label, detail: `Offline · port held by ${otherEditor}`, ok: false, warn: wrongProject };
    return { label, detail: offline, ok: false };
  };
  return [
    row(
      "UEFN MCP",
      editor === "uefn" || (!editor && Boolean(status.epic_mcp_online)),
      "Unreal Editor",
      `Offline${reason ? ` · ${reason}` : ""}`,
    ),
    row("Unreal MCP", editor === "unreal", "UEFN", "Offline · open Unreal Editor 5.8+ with Unreal MCP"),
  ];
}

function Row({ label, detail, ok, warn, action }: RowProps & { action?: ReactNode }) {
  const state = warn ? "warn" : ok ? "ok" : "off";
  return (
    <div className={`connection-status-menu-row is-${state}`}>
      <span className="connection-status-menu-dot" aria-hidden />
      <div className="connection-status-menu-text">
        <span className="connection-status-menu-label">
          {label}
          {action}
        </span>
        <span className="connection-status-menu-detail">{detail}</span>
      </div>
    </div>
  );
}

/** Restart the UEFN listener; when it is down, Ducky turns Python on in UEFN so it starts. */
function RestartListenerButton({ onResult }: { onResult: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const api = getApi();
  if (!api?.restart_listener) return null;
  const restart = async () => {
    setBusy(true);
    onResult("Restarting…");
    try {
      const res = await api.restart_listener!();
      onResult(res?.message || (res?.ok ? "Listener restarted." : "Listener restart failed."));
    } catch (e) {
      onResult(`Listener restart failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      className={`connection-status-menu-action${busy ? " is-busy" : ""}`}
      title="Restart UEFN listener"
      aria-label="Restart UEFN listener"
      disabled={busy}
      onClick={() => void restart()}
    >
      {busy ? <Icons.Spinner /> : <Icons.Refresh />}
    </button>
  );
}

export interface ConnectionStatusDropdownProps {
  status: ListenerStatus;
  projectName?: string;
  hasStoreUpdates?: boolean;
  onOpenSettings: () => void;
  readonly?: boolean;
  /** Remote/mobile: recent projects list (replaces the header project dropdown). */
  extra?: ReactNode;
  /** Phones: a first Tabs tab listing the open editor tabs (MobileTabSwitcher portals into it). */
  tabsTab?: boolean;
  /** Phones: the header's tool buttons as one icon row above Settings; `close` shuts the menu. */
  tools?: (close: () => void) => ReactNode;
  /** Any pick in the menu (not a tab switch): watching a PC window, this goes back to Ducky. */
  onMenuAction?: () => void;
}

/**
 * Header duck icon: Settings, then tabs. Projects (phone/remote only, from ``extra``): the
 * recent projects list. Connections: Ducky MCP, the UEFN listener,
 * Epic MCP and plugin connections. Online: your other UEFN Duckies and the people plugins
 * list (``api.people``); the icon's badge counts who is online there.
 */
export function ConnectionStatusDropdown({
  status,
  projectName,
  hasStoreUpdates = false,
  onOpenSettings,
  readonly = false,
  extra,
  tabsTab = false,
  tools,
  onMenuAction,
}: ConnectionStatusDropdownProps) {
  const onMenuActionRef = useRef(onMenuAction);
  onMenuActionRef.current = onMenuAction;
  const [open, setOpen] = useState(false);
  // A phone puts its open tabs here as the first tab; a remote view its project list next.
  const [tab, setTab] = useState<"tabs" | "projects" | "connections" | "online">(
    tabsTab ? "tabs" : extra ? "projects" : "connections",
  );
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const [restartNote, setRestartNote] = useState("");
  const { duckies, error: duckiesError, reload: reloadDuckies } = useAccountDuckies();
  const peopleSections = (status.plugin_people ?? []).filter((s) => s.people.length);
  const hasOnlineTab = duckies.some((d) => !d.mine) || peopleSections.length > 0;
  const online = onlineCount(duckies, peopleSections);
  const badge = badgeText(online);
  const showOnline = hasOnlineTab && tab === "online";
  const showProjects = Boolean(extra) && tab === "projects";
  const showOpenTabs = tabsTab && tab === "tabs";
  const showConnections = !showOnline && !showProjects && !showOpenTabs;
  const hasTabs = hasOnlineTab || Boolean(extra) || tabsTab;

  useEffect(() => {
    if (open && showOnline) reloadDuckies();
  }, [open, showOnline, reloadDuckies]);
  const triggerRef = useRef<HTMLButtonElement | HTMLSpanElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const isOnline = Boolean(status.online);
  const isWedged = Boolean(status.wedged);
  const race = Boolean(status.listener_init_race) && !isOnline;
  const uptimeSec = useListenerUptimeSec(open) ?? status.uptime_sec;

  const pop = useMenuPop(open);
  const close = useCallback(() => setOpen(false), []);
  // The tab list is portaled in from the editor, so its picks never reach the menu's click handler.
  const openTabsRef = useCallback(
    (el: HTMLDivElement | null) =>
      setHeaderTabsSlot(el, () => {
        onMenuActionRef.current?.();
        close();
      }),
    [close],
  );
  useDropdownDismiss(open, close, menuRef, triggerRef);

  // Closing keeps the last spot so the menu zooms back into the icon.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const update = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      setMenuPos(computeMenuPosition(trigger));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  // A restart note is about that click: drop it on close, or once the listener comes up.
  useEffect(() => {
    if (!open) setRestartNote("");
  }, [open]);
  useEffect(() => {
    if (isOnline) setRestartNote("");
  }, [isOnline]);

  const duckyDetail = isWedged
    ? "Wedged — reload_listener once, then stay on workspace_*"
    : isOnline
      ? `Connected · port ${status.port ?? 4200}${uptimeSec ? ` · up ${Math.floor(uptimeSec)}s` : ""}`
      : race
        ? "Offline — listener init race; stay on workspace_*"
        : "Offline — open UEFN + start listener";

  const [uefnMcpRow, unrealMcpRow] = epicMcpRows(status);

  const panelProject = projectName?.trim() || "";
  const duckyMcpDetail = panelProject ? `Connected · ${panelProject}` : "Connected";

  const title = status.status_text?.trim() || (isOnline ? "Online" : "Offline");

  const menu =
    pop.mounted && menuPos && !readonly ? (
      <div
        ref={menuRef}
        className={`connection-status-menu connection-status-menu--portaled no-drag menu-pop${pop.closing ? " is-closing" : ""}`}
        style={{ top: menuPos.top, left: menuPos.left, transformOrigin: popOrigin(triggerRef.current, menuPos) }}
        role="dialog"
        aria-label="Connection status"
        onClickCapture={(e) => {
          const btn = (e.target as Element).closest("button");
          if (!btn || btn.closest('[role="tablist"]') || btn.classList.contains("connection-status-menu-action")) return;
          onMenuActionRef.current?.();
        }}
      >
        {tools ? <div className="connection-status-tools">{tools(close)}</div> : null}
        <button
          type="button"
          className="connection-status-menu-settings"
          onClick={() => {
            setOpen(false);
            onOpenSettings();
          }}
        >
          <Icons.Settings />
          <span>Settings</span>
          {hasStoreUpdates ? <span className="store-update-dot store-update-dot--inline" /> : null}
        </button>
        {isFramedWebView() ? (
          <button
            type="button"
            className="connection-status-menu-settings"
            onClick={() => {
              setOpen(false);
              window.parent.postMessage({ type: "ud-remote-close" }, "*");
            }}
          >
            <Icons.Close />
            <span>Close web view</span>
          </button>
        ) : null}
        {hasTabs ? (
          <div className="connection-status-tabs" role="tablist" aria-label="Status">
            {tabsTab ? (
              <button
                type="button"
                role="tab"
                aria-selected={showOpenTabs}
                className={`connection-status-tab${showOpenTabs ? " is-active" : ""}`}
                onClick={() => setTab("tabs")}
              >
                Tabs
              </button>
            ) : null}
            {extra ? (
              <button
                type="button"
                role="tab"
                aria-selected={showProjects}
                className={`connection-status-tab${showProjects ? " is-active" : ""}`}
                onClick={() => setTab("projects")}
              >
                Projects
              </button>
            ) : null}
            <button
              type="button"
              role="tab"
              aria-selected={showConnections}
              className={`connection-status-tab${showConnections ? " is-active" : ""}`}
              onClick={() => setTab("connections")}
            >
              Connections
            </button>
            {hasOnlineTab ? (
              <button
                type="button"
                role="tab"
                aria-selected={showOnline}
                className={`connection-status-tab${showOnline ? " is-active" : ""}`}
                onClick={() => setTab("online")}
              >
                Online
                {badge ? <span className="connection-status-tab-count">{badge}</span> : null}
              </button>
            ) : null}
          </div>
        ) : null}
        {showOpenTabs ? (
          <div className="connection-status-open-tabs" ref={openTabsRef} />
        ) : showProjects ? (
          extra
        ) : showOnline ? (
          <>
            <DuckiesRows duckies={duckies} error={duckiesError} onPicked={() => setOpen(false)} />
            {peopleSections.map((section) => (
              <div key={section.id}>
                <div className="connection-status-menu-head">{section.label}</div>
                {[...section.people]
                  .sort((a, b) => Number(b.online) - Number(a.online) || a.name.localeCompare(b.name))
                  .map((p) => (
                    <Row key={p.id} label={p.name} detail={p.detail || (p.online ? "Online" : "Offline")} ok={p.online} />
                  ))}
              </div>
            ))}
          </>
        ) : (
          <>
            {hasTabs ? null : <div className="connection-status-menu-head">Connections</div>}
            <Row label="Ducky MCP" detail={duckyMcpDetail} ok />
            <Row
              label="UEFN listener"
              detail={restartNote || duckyDetail}
              ok={isOnline && !isWedged}
              warn={isWedged || race}
              action={<RestartListenerButton onResult={setRestartNote} />}
            />
            <Row {...uefnMcpRow} />
            <Row {...unrealMcpRow} />
            {(status.plugin_connections ?? []).map((row) => (
              <Row
                key={row.id}
                label={row.label}
                detail={row.detail}
                ok={row.online}
                warn={row.warn}
              />
            ))}
          </>
        )}
      </div>
    ) : null;

  if (readonly) {
    return (
      <span className="connection-status-btn connection-status-btn--readonly" title={title}>
        <ConnectionStatusIcon isOnline={isOnline} isWedged={isWedged} />
      </span>
    );
  }

  return (
    <div className="connection-status-root">
      <button
        ref={triggerRef as RefObject<HTMLButtonElement>}
        type="button"
        className={`no-drag connection-status-btn${open ? " is-active" : ""}${hasStoreUpdates ? " has-store-update" : ""}`}
        title={title}
        aria-label={title}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        <ConnectionStatusIcon isOnline={isOnline} isWedged={isWedged} />
        {hasStoreUpdates ? <span className="store-update-dot" aria-label="Store updates available" /> : null}
        {badge ? (
          <span className="icon-btn-badge icon-btn-badge--online" aria-label={`${online} online`}>
            {badge}
          </span>
        ) : null}
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
