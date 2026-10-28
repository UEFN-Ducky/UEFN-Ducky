import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { confirmFromHost } from "../contexts/ConfirmModalContext";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import type { SandboxApp } from "../types/panel";

const MENU_WIDTH = 440;
const FILTERS = [
  { id: "all", label: "All" },
  { id: "running", label: "Running" },
  { id: "stopped", label: "Stopped" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];
const MENU_GAP = 6;
const POLL_OPEN_MS = 5000;
// Closed, the list only needs to notice a new app for the button to appear.
const POLL_CLOSED_MS = 30000;

/** The app's own favicon while it runs, else the last screenshot of it, else a box. */
function AppIcon({ app }: { app: SandboxApp }) {
  const sources = [app.running && app.ports[0] ? `http://localhost:${app.ports[0]}/favicon.ico` : "", app.picture]
    .filter(Boolean);
  const key = sources.join("|");
  const [at, setAt] = useState(0);
  useEffect(() => setAt(0), [key]);
  const src = sources[at];
  return src ? (
    <img className="sandbox-app-icon" src={src} alt="" draggable={false} onError={() => setAt((i) => i + 1)} />
  ) : (
    <span className="sandbox-app-icon"><Icons.Box /></span>
  );
}

/** Header menu of every app a sandbox built, in any chat: open, run, stop, delete. Hidden until one exists. */
export function SandboxAppsDropdown() {
  const [apps, setApps] = useState<SandboxApp[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    const got = await getApi()?.sandbox_apps?.().catch(() => null);
    if (got?.ok) setApps(got.apps ?? []);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), open ? POLL_OPEN_MS : POLL_CLOSED_MS);
    return () => window.clearInterval(timer);
  }, [open, refresh]);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setMenuPos({ top: rect.bottom + MENU_GAP, left: Math.max(8, Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 8)) });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      if (target instanceof Element && target.closest("[data-dropdown-keep]")) return;
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

  if (!apps.length) return null;

  const act = async (key: string, work: () => Promise<unknown> | undefined) => {
    setBusy(key);
    try {
      await work();
    } finally {
      setBusy("");
      void refresh();
    }
  };
  const openHere = (port: number) => void getApi()?.plugin_call?.("browser", "open_page", { url: `localhost:${port}` });
  const openOutside = (port: number) => void getApi()?.open_external_url?.(`http://localhost:${port}`);
  const remove = async (app: SandboxApp) => {
    const others = apps.filter((a) => a.conv_id === app.conv_id && a.name !== app.name).map((a) => a.name);
    // The header also renders outside the modal provider (focus view, tests): no dialog, no delete.
    const ok = await confirmFromHost({
      title: `Delete ${app.name}?`,
      message: `Stops it and deletes the sandbox it was built in${others.length ? ` (with ${others.join(", ")})` : ""}. Your projects don't change.`,
      confirmLabel: "Delete",
      danger: true,
    }).catch(() => false);
    if (ok) await act(`del-${app.conv_id}`, () => getApi()?.sandbox_delete?.(app.conv_id));
  };

  const running = apps.filter((a) => a.running).length;
  const title = running ? `Apps · ${running} running` : "Apps";
  const q = query.trim().toLowerCase();
  const shown = apps.filter((a) =>
    (filter === "all" || a.running === (filter === "running")) &&
    (!q || a.name.toLowerCase().includes(q) || (a.chat ?? "").toLowerCase().includes(q)));
  const counts: Record<Filter, number> = { all: apps.length, running, stopped: apps.length - running };
  const menu = open && menuPos ? (
    <div ref={menuRef} className="terminal-header-menu terminal-header-menu--portaled sandbox-apps-menu no-drag"
      style={{ top: menuPos.top, left: menuPos.left, width: MENU_WIDTH }}>
      <div className="sandbox-apps-head">
        <div className="sandbox-apps-title">
          <Icons.Grid />
          <span>Apps you built</span>
        </div>
        <input className="sandbox-apps-search" type="search" placeholder="Search apps or chats…" value={query}
          aria-label="Search apps" autoFocus onChange={(e) => setQuery(e.target.value)} />
        <div className="sandbox-apps-filters" role="group" aria-label="Filter apps">
          {FILTERS.map((f) => (
            <button key={f.id} type="button" aria-pressed={filter === f.id}
              className={`sandbox-apps-filter${filter === f.id ? " is-active" : ""}`} onClick={() => setFilter(f.id)}>
              {f.label} <span className="sandbox-apps-filter-count">{counts[f.id]}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="terminal-header-list sandbox-apps-list">
        {!shown.length ? <p className="terminal-header-empty">No apps match.</p> : null}
        {shown.map((app) => {
          const id = `${app.conv_id}:${app.name}`;
          const port = app.ports[0];
          return (
            <div key={id} className={`terminal-header-item sandbox-app-row${app.running ? " is-running" : ""}`}>
              <button type="button" className="terminal-header-item-main" disabled={!port}
                title={port ? `Open localhost:${port} in the Browser tab` : "Run it to open it"}
                onClick={() => { if (port) { openHere(port); setOpen(false); } }}>
                <AppIcon app={app} />
                <span className="sandbox-app-text">
                  <span className="terminal-header-item-name">{app.name}</span>
                  <span className="terminal-header-item-owner">
                    {app.running ? `running${port ? ` · :${port}` : ""}` : "stopped"}
                    {app.chat ? ` · ${app.chat}` : ""}
                  </span>
                </span>
              </button>
              {port ? (
                <button type="button" className="terminal-header-item-close icon-btn no-drag" title="Open in your browser"
                  aria-label={`Open ${app.name} in your browser`} onClick={() => openOutside(port)}>
                  <Icons.Globe />
                </button>
              ) : null}
              {app.running ? (
                <button type="button" className="terminal-header-item-close icon-btn no-drag" title="Stop"
                  aria-label={`Stop ${app.name}`} disabled={busy === id}
                  onClick={() => void act(id, () => getApi()?.sandbox_stop_program?.(app.conv_id, app.pid))}>
                  <Icons.Stop />
                </button>
              ) : (
                <button type="button" className="terminal-header-item-close icon-btn no-drag" title="Run"
                  aria-label={`Run ${app.name}`} disabled={busy === id}
                  onClick={() => void act(id, () => getApi()?.sandbox_relaunch?.(app.conv_id, app.name))}>
                  <Icons.Play />
                </button>
              )}
              <button type="button" className="terminal-header-item-close icon-btn no-drag" title="Delete"
                aria-label={`Delete ${app.name}`} disabled={busy === `del-${app.conv_id}`}
                onClick={() => void remove(app)}>
                <Icons.Trash />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  ) : null;

  return (
    <div className="terminal-header-root">
      <button ref={triggerRef} type="button" title={title} aria-label={title}
        className={`connection-status-btn terminal-header-trigger${open ? " is-active" : ""}${running ? " has-terminals" : ""}`}
        onClick={() => setOpen(!open)}>
        <Icons.Grid />
        {running ? <span className="terminal-header-badge">{running}</span> : null}
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
