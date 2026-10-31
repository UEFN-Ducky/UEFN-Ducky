import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { confirmFromHost } from "../contexts/ConfirmModalContext";
import { getApi } from "../hooks/usePanelApi";
import { popOrigin, useDropdownDismiss, useMenuPop } from "./DropdownPanel";
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
// Closed, the list only needs to keep the running badge roughly current.
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

/** Header menu of every app a sandbox built, in any chat: open, run, stop, delete. Always shown. */
export function SandboxAppsDropdown() {
  const [apps, setApps] = useState<SandboxApp[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  /** Ticked apps (`conv:name`) for Stop / Delete on several at once. */
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [anchor, setAnchor] = useState("");
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const pop = useMenuPop(open);
  const close = useCallback(() => setOpen(false), []);
  useDropdownDismiss(open, close, menuRef, triggerRef);

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

  const removeMany = async (picked: SandboxApp[]) => {
    const convs = [...new Set(picked.map((a) => a.conv_id))];
    const names = new Set(picked.map((a) => a.name));
    // A sandbox goes as a whole, so its other apps go with the ticked one.
    const others = apps.filter((a) => convs.includes(a.conv_id) && !names.has(a.name)).map((a) => a.name);
    const ok = await confirmFromHost({
      title: `Delete ${picked.length} app${picked.length === 1 ? "" : "s"}?`,
      message: `Stops ${picked.length === 1 ? "it" : "them"} and deletes the sandbox${convs.length === 1 ? "" : "es"} they were built in${others.length ? ` (with ${others.join(", ")})` : ""}. Your projects don't change.`,
      confirmLabel: "Delete",
      danger: true,
    }).catch(() => false);
    if (!ok) return;
    await act("bulk", async () => {
      for (const conv of convs) await getApi()?.sandbox_delete?.(conv);
    });
    setSelected(new Set());
  };

  const stopMany = (picked: SandboxApp[]) =>
    void act("bulk", async () => {
      for (const a of picked) if (a.running) await getApi()?.sandbox_stop_program?.(a.conv_id, a.pid);
    });

  const running = apps.filter((a) => a.running).length;
  const title = running ? `Apps · ${running} running` : "Apps";
  const q = query.trim().toLowerCase();
  const shown = apps.filter((a) =>
    (filter === "all" || a.running === (filter === "running")) &&
    (!q || a.name.toLowerCase().includes(q) || (a.chat ?? "").toLowerCase().includes(q)));
  const counts: Record<Filter, number> = { all: apps.length, running, stopped: apps.length - running };
  const appId = (a: SandboxApp) => `${a.conv_id}:${a.name}`;
  // Only what is on screen: a filter never hides an app that a bulk delete would take.
  const picked = shown.filter((a) => selected.has(appId(a)));
  const toggle = (id: string, range: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      const ids = shown.map(appId);
      const from = ids.indexOf(anchor);
      const to = ids.indexOf(id);
      if (range && from >= 0 && to >= 0) {
        for (const x of ids.slice(Math.min(from, to), Math.max(from, to) + 1)) next.add(x);
      } else if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setAnchor(id);
  };
  const menu = pop.mounted && menuPos ? (
    <div ref={menuRef}
      className={`terminal-header-menu terminal-header-menu--portaled sandbox-apps-menu no-drag menu-pop${pop.closing ? " is-closing" : ""}`}
      style={{ top: menuPos.top, left: menuPos.left, width: MENU_WIDTH, transformOrigin: popOrigin(triggerRef.current, menuPos) }}>
      <div className="sandbox-apps-head">
        <div className="sandbox-apps-title">
          <Icons.Grid />
          <span>Apps you built</span>
        </div>
        {apps.length ? (<>
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
        {picked.length ? (
          <div className="sandbox-apps-selection" role="toolbar" aria-label="Selected apps">
            <span className="sandbox-apps-selection-count">{picked.length} selected</span>
            {picked.length < shown.length ? (
              <button type="button" className="sandbox-apps-selection-btn"
                onClick={() => setSelected(new Set(shown.map(appId)))}>Select all</button>
            ) : null}
            {picked.some((a) => a.running) ? (
              <button type="button" className="sandbox-apps-selection-btn" disabled={busy === "bulk"}
                onClick={() => stopMany(picked)}>
                <Icons.Stop /> Stop
              </button>
            ) : null}
            <button type="button" className="sandbox-apps-selection-btn is-danger" disabled={busy === "bulk"}
              onClick={() => void removeMany(picked)}>
              <Icons.Trash /> Delete {picked.length}
            </button>
            <button type="button" className="terminal-header-item-close icon-btn no-drag" title="Clear selection"
              aria-label="Clear selection" onClick={() => setSelected(new Set())}>
              <Icons.Close />
            </button>
          </div>
        ) : null}
        </>) : null}
      </div>
      <div className="terminal-header-list sandbox-apps-list">
        {!apps.length ? (
          <div className="sandbox-apps-empty">
            <span className="sandbox-app-icon"><Icons.Grid /></span>
            <strong>No apps yet</strong>
            <span>When a chat builds and runs an app in its sandbox, it shows up here to open, run, stop or delete.</span>
          </div>
        ) : !shown.length ? <p className="terminal-header-empty">No apps match.</p> : null}
        {shown.map((app) => {
          const id = `${app.conv_id}:${app.name}`;
          const port = app.ports[0];
          return (
            <div key={id}
              className={`terminal-header-item sandbox-app-row${app.running ? " is-running" : ""}${selected.has(id) ? " is-selected" : ""}${picked.length ? " is-selecting" : ""}`}>
              <input type="checkbox" className="sandbox-app-check" checked={selected.has(id)}
                aria-label={`Select ${app.name}`}
                onChange={(e) => toggle(id, (e.nativeEvent as MouseEvent).shiftKey)} />
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
