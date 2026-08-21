import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { ConnectionStatusIcon } from "./ConnectionStatusIcon";
import { Icons } from "../icons/Icons";
import type { ListenerStatus } from "../types/panel";

const MENU_WIDTH = 300;
const MENU_GAP = 6;
const HOVER_OPEN_MS = 180;
const HOVER_CLOSE_MS = 220;

function computeMenuPosition(trigger: HTMLElement): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  let left = rect.left;
  if (left + MENU_WIDTH > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - MENU_WIDTH - 8);
  }
  if (left < 8) left = 8;
  return { top: rect.bottom + MENU_GAP, left };
}

function Row({
  label,
  detail,
  ok,
  warn,
}: {
  label: string;
  detail: string;
  ok: boolean;
  warn?: boolean;
}) {
  const state = warn ? "warn" : ok ? "ok" : "off";
  return (
    <div className={`connection-status-menu-row is-${state}`}>
      <span className="connection-status-menu-dot" aria-hidden />
      <div className="connection-status-menu-text">
        <span className="connection-status-menu-label">{label}</span>
        <span className="connection-status-menu-detail">{detail}</span>
      </div>
    </div>
  );
}

export interface ConnectionStatusDropdownProps {
  status: ListenerStatus;
  projectName?: string;
  hasStoreUpdates?: boolean;
  onOpenSettings: () => void;
  readonly?: boolean;
}

/** Header duck icon: hover/click shows Ducky listener + Epic MCP connection panel. */
export function ConnectionStatusDropdown({
  status,
  projectName,
  hasStoreUpdates = false,
  onOpenSettings,
  readonly = false,
}: ConnectionStatusDropdownProps) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | HTMLSpanElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const openTimer = useRef<number | null>(null);
  const closeTimer = useRef<number | null>(null);

  const isOnline = Boolean(status.online);
  const isWedged = Boolean(status.wedged);
  const epicOnline = Boolean(status.epic_mcp_online);
  const race = Boolean(status.listener_init_race) && !isOnline;

  const clearTimers = () => {
    if (openTimer.current != null) window.clearTimeout(openTimer.current);
    if (closeTimer.current != null) window.clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  };

  const scheduleOpen = () => {
    if (readonly) return;
    clearTimers();
    openTimer.current = window.setTimeout(() => setOpen(true), HOVER_OPEN_MS);
  };

  const scheduleClose = () => {
    if (readonly) return;
    clearTimers();
    closeTimer.current = window.setTimeout(() => setOpen(false), HOVER_CLOSE_MS);
  };

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setMenuPos(null);
      return;
    }
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

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => () => clearTimers(), []);

  const duckyDetail = isWedged
    ? "Wedged — restart UEFN"
    : isOnline
      ? `Connected · port ${status.port ?? 4200}${status.uptime_sec ? ` · up ${Math.floor(status.uptime_sec)}s` : ""}`
      : race
        ? "Offline — restart UEFN to reconnect"
        : "Offline — open UEFN + start listener";

  const epicReason = String(status.epic_mcp_reason || "").trim();
  const epicDetail = epicOnline
    ? `Connected · ${status.epic_mcp_url || "http://127.0.0.1:8000/mcp"}`
    : epicReason === "disabled"
      ? "Disabled in Settings → MCPs"
      : `Offline${epicReason ? ` · ${epicReason}` : ""}`;

  const panelProject = projectName?.trim() || "";
  const uefnProject = status.uefn_project_name?.trim() || "";
  const duckyMcpOk = Boolean(isOnline && status.project_match !== false && (uefnProject || panelProject));
  const duckyMcpWarn =
    status.project_match === false || (!isOnline && Boolean(uefnProject || panelProject));
  let duckyMcpDetail: string;
  if (status.project_match === false && panelProject && uefnProject) {
    duckyMcpDetail = `Mismatch · UEFN ${uefnProject} ≠ panel ${panelProject}`;
  } else if (duckyMcpOk) {
    duckyMcpDetail = `Connected · ${uefnProject || panelProject}`;
  } else if (!isOnline && (panelProject || uefnProject)) {
    duckyMcpDetail = `Offline · ${panelProject || uefnProject}`;
  } else if (panelProject || uefnProject) {
    duckyMcpDetail = panelProject || uefnProject;
  } else {
    duckyMcpDetail = isOnline ? "Connected" : "No project selected";
  }

  const title = status.status_text?.trim() || (isOnline ? "Online" : "Offline");

  const menu =
    open && menuPos && !readonly ? (
      <div
        ref={menuRef}
        className="connection-status-menu connection-status-menu--portaled no-drag"
        style={{ top: menuPos.top, left: menuPos.left }}
        onMouseEnter={() => {
          clearTimers();
          setOpen(true);
        }}
        onMouseLeave={scheduleClose}
        role="dialog"
        aria-label="Connection status"
      >
        <div className="connection-status-menu-head">Connections</div>
        <Row label="Ducky listener" detail={duckyDetail} ok={isOnline && !isWedged} warn={isWedged || race} />
        <Row label="UEFN MCP" detail={epicDetail} ok={epicOnline} />
        <Row label="Ducky MCP" detail={duckyMcpDetail} ok={duckyMcpOk} warn={duckyMcpWarn} />
        {race && !isOnline ? (
          <p className="connection-status-menu-note">
            Restart UEFN once to reconnect the Ducky listener (port 4200).
          </p>
        ) : null}
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
        onClick={() => {
          clearTimers();
          setOpen((v) => !v);
        }}
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
      >
        <ConnectionStatusIcon isOnline={isOnline} isWedged={isWedged} />
        {hasStoreUpdates ? <span className="store-update-dot" aria-label="Store updates available" /> : null}
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
