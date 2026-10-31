import { useEffect, useRef, useState, type ReactNode } from "react";
import "./plugin-ui.css";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import type { PluginScopeStatus, TeamPluginRow } from "../types/panel";
import { FileTypeIcon } from "../verse-editor/components/FileTypeIcon";
import { readActiveTeam, type ActiveTeam } from "./activeTeamStore";
import { PaneCard } from "./PaneCard";
import { PluginScopeDrawer } from "./PluginScopeDrawer";
import { formatBytes, listText, outgoingStateText, type ScopeTab } from "./scopeBarText";
import { useTeamScope, type TeamScope } from "./teamScopeStore";

export type TeamPluginsSection = "overview" | ScopeTab;

/** Rows a folded-open plugin shows before "Open". */
const PREVIEW = 5;

const INTRO: Record<TeamPluginsSection, string> = {
  overview: "Every plugin on this team. Open one for its changes, history and settings.",
  incoming: "Teammates' changes on their way to this PC, per plugin.",
  changes: "Your changes on this PC, per plugin, before they go to the team.",
  conflicts: "Where you and a teammate changed the same thing while apart.",
  locks: "What's locked, per plugin.",
  history: "Every change sent to the team, per plugin.",
  people: "Who's here, per plugin.",
  settings: "Each plugin's own team settings.",
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function sourceText(row: TeamPluginRow): string {
  if (row.source === "workflows") return plural(row.items, "workflow");
  if (row.source === "tab") return "On this tab · not on this team";
  const kind = row.source === "store" ? "Store plugin" : row.source === "ai" ? "AI plugin" : "Private plugin";
  return `${kind} · ${plural(row.items, "item")}`;
}

/** The plugin's icon, else Workflows' own, else its first letter. */
export function TeamPluginIcon({ row, size = 28 }: { row: TeamPluginRow; size?: number }) {
  return (
    <span className="team-plugin-icon" style={{ width: size, height: size }} aria-hidden>
      {row.icon ? <img src={row.icon} alt="" draggable={false} />
        : row.source === "workflows" ? <Icons.Workflow /> : <span>{row.label.slice(0, 1).toUpperCase()}</span>}
    </span>
  );
}

/** What a plugin's header says on the right in this section, and whether it needs looking at. */
function badge(row: TeamPluginRow, section: TeamPluginsSection): { text: string; tone?: "warn" | "error" } {
  if (section === "conflicts" || (section === "overview" && row.conflicts)) {
    return row.conflicts ? { text: plural(row.conflicts, "conflict"), tone: "error" } : { text: "None" };
  }
  if (row.state === "offline" || row.state === "error") return { text: "Couldn't sync", tone: "error" };
  if (section === "incoming" || section === "overview") {
    if (row.blocked.length) return { text: `${row.blocked.length} failed`, tone: "warn" };
    if (row.incoming) return { text: `${row.incoming} coming in` };
  }
  if (section === "changes" || section === "overview") {
    const out = row.pending || row.outgoing.length;
    if (out) return { text: `${out} to send` };
  }
  return { text: section === "incoming" || section === "changes" || section === "overview" ? "Up to date" : "" };
}

function meta(row: TeamPluginRow, section: TeamPluginsSection): string {
  const parts = [sourceText(row)];
  if ((section === "incoming" || section === "overview") && row.incoming && row.incomingBy.length) {
    parts.push(`from ${listText(row.incomingBy)}`);
  }
  return parts.join(" · ");
}

/** One ledger line in a plugin's folded-open body. */
function LedgerLine({ verb, name, detail, tone }: { verb: string; name: string; detail?: string; tone?: "blocked" }) {
  return (
    <div className={`changes-row changes-row--file${tone ? ` changes-row--${tone}` : ""}`}>
      <div className="changes-row-line">
        <span className={`changes-row-verb${tone ? " changes-row-verb--blocked" : ""}`}>{verb}</span>
        <span className="changes-row-icon"><FileTypeIcon path={name} size={13} /></span>
        <span className="changes-row-name" title={name}>{name}</span>
        {detail ? <span className="changes-row-detail">{detail}</span> : null}
      </div>
    </div>
  );
}

/** The folded-open body: the first few things of this section, and its buttons. */
function Preview({ row, section, onOpen }: { row: TeamPluginRow; section: TeamPluginsSection; onOpen: () => void }) {
  const [busy, setBusy] = useState(false);
  const get = async () => {
    setBusy(true);
    try {
      await getApi()?.plugin_scope_pull?.(row.pluginId);
    } finally {
      setBusy(false);
      void readActiveTeam();
    }
  };
  const lines: ReactNode[] = [];
  if (section === "incoming" || section === "overview") {
    if (row.incoming) {
      lines.push(<LedgerLine key="in" verb="incoming" name={plural(row.incoming, "change")}
        detail={row.held ? `waits for Update now${row.incomingBy.length ? ` · ${listText(row.incomingBy)}` : ""}`
          : row.incomingBy.length ? listText(row.incomingBy) : "coming in now"} />);
    }
    for (const b of row.blocked.slice(0, PREVIEW)) {
      lines.push(<LedgerLine key={`b:${b.kind}:${b.key}`} verb="failed" tone="blocked" name={b.name || b.key}
        detail={b.error || "Couldn't be applied here"} />);
    }
  }
  if (section === "changes" || section === "overview") {
    for (const o of row.outgoing.slice(0, PREVIEW)) {
      lines.push(<LedgerLine key={`o:${o.kind}:${o.key}`} verb={o.deleted ? "delete" : "send"} name={o.name || o.key}
        tone={o.state === "blocked" || o.state === "refused" ? "blocked" : undefined}
        detail={[o.size ? formatBytes(o.size) : "", outgoingStateText(o.state, o.reason, o.attempts)].filter(Boolean).join(" · ")} />);
    }
  }
  const canGet = (section === "incoming" || section === "overview") && Boolean(getApi()?.plugin_scope_pull);
  return (
    <div className="team-ledger__body">
      {lines.length ? lines : <p className="team-ledger__empty">Nothing here for {row.label}. Open it for the rest.</p>}
      <div className="team-ledger__actions">
        {canGet ? (
          <button type="button" className="plugin-scope-btn plugin-scope-btn--row" disabled={busy} onClick={() => void get()}>
            {row.held && row.incoming ? "Update now" : "Get now"}
          </button>
        ) : null}
        <button type="button" className="plugin-scope-btn plugin-scope-btn--row" onClick={onOpen}>Details</button>
      </div>
    </div>
  );
}

/** The plugin on screen as a row, while it isn't on the team (it shows Local or another team). */
function paneRow(pane: TeamScope): TeamPluginRow {
  return {
    pluginId: pane.pluginId, label: pane.plugin, icon: "", source: "tab", incoming: 0, incomingBy: [], held: false,
    outgoing: [], pending: 0, conflicts: pane.conflicts, blocked: [], state: "", error: "", items: 0,
  };
}

/** The slid-in view of one plugin: its header with Back, the plugin on screen's own card (status,
 * Send / Get, notes, whose data it shows), and its source control on this section. */
function PluginDetail({ row, team, section, onBack, onSection }: {
  row: TeamPluginRow; team: ActiveTeam; section: TeamPluginsSection; onBack: () => void; onSection: (s: ScopeTab) => void;
}) {
  const pane = useTeamScope();
  const own = pane?.pluginId === row.pluginId ? pane : null;
  const [read, setRead] = useState<PluginScopeStatus | null>(null);
  const [error, setError] = useState("");
  const load = async () => {
    try {
      const got = await getApi()?.plugin_scope_status?.(row.pluginId);
      if (got && got.ok !== false) setRead(got);
      else setError(got?.error || "Couldn't read this plugin's team copy.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  useEffect(() => {
    if (!own) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.pluginId, own === null]);
  const status = own?.status ?? read;
  const tab: ScopeTab = section === "overview" ? "changes" : section;
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => backRef.current?.focus({ preventScroll: true }), []);

  return (
    <div className="team-detail">
      <div className="team-detail__head">
        <button ref={backRef} type="button" className="changeset-btn changeset-btn--icon team-detail__back" aria-label="Back"
          title="Back" onClick={onBack}>
          <Icons.ChevronLeft />
        </button>
        <TeamPluginIcon row={row} size={24} />
        <span className="team-detail__title">
          <span className="changes-run-name">{row.label}</span>
          <span className="changes-run-meta">{team.label} · {sourceText(row)}</span>
        </span>
      </div>
      {own && section !== "overview" ? <PaneCard scope={own} onSection={onSection} /> : null}
      {error ? <p className="plugin-scope-msg plugin-scope-msg--error" role="alert">{error}</p> : null}
      {status ? (
        <PluginScopeDrawer pluginId={row.pluginId} status={status} tab={tab} onTab={onSection} hideTabs
          onJoin={own?.actions.joinLive ? (room) => void own.actions.joinLive?.(room) : undefined}
          onChanged={async (changed) => {
            if (own) await own.actions.refresh(changed);
            else await load();
            void readActiveTeam();
          }} />
      ) : !error ? <p className="plugin-scope-empty">Loading…</p> : null}
    </div>
  );
}

/**
 * Every plugin on the team (Workflows too) as Ledger groups: icon, name, what it is, and what
 * needs looking at in this section. A header folds its first few lines open; Details (or the
 * name) slides the list out to the left and that plugin's own view in, with Back.
 */
export function TeamPlugins({ team, section, onSection, openId, onOpened }: {
  team: ActiveTeam; section: TeamPluginsSection; onSection: (s: ScopeTab) => void;
  /** Slide this plugin in now (a status press on its card asked for it). */
  openId?: string | null;
  onOpened?: () => void;
}) {
  const pane = useTeamScope();
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [detail, setDetail] = useState<string | null>(null);
  const heads = useRef<Record<string, HTMLDivElement | null>>({});
  const rows = pane && !team.plugins.some((p) => p.pluginId === pane.pluginId) ? [...team.plugins, paneRow(pane)] : team.plugins;
  useEffect(() => setDetail(null), [section, team.current]);
  useEffect(() => {
    if (!openId || !rows.some((p) => p.pluginId === openId)) return;
    setDetail(openId);
    onOpened?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId, section, rows.length]);
  const row = detail ? rows.find((p) => p.pluginId === detail) : undefined;
  const toggle = (id: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (!next.delete(id)) next.add(id);
    return next;
  });
  const back = () => {
    const id = detail;
    setDetail(null);
    if (id) window.requestAnimationFrame(() => heads.current[id]?.focus({ preventScroll: true }));
  };

  return (
    <div className={`team-slide${row ? " is-detail" : ""}`}>
      <div className="team-slide__track">
        <div className="team-slide__panel team-slide__panel--list" aria-hidden={row ? true : undefined}>
          <p className="team-slide__intro">{INTRO[section]}</p>
          {rows.length ? (
            <div className="team-ledger" role="list" aria-label={`Plugins on ${team.label}`}>
              {rows.map((p) => {
                const isOpen = open.has(p.pluginId);
                const b = badge(p, section);
                return (
                  <div key={p.pluginId} className="team-ledger__group" role="listitem">
                    <div ref={(el) => { heads.current[p.pluginId] = el; }}
                      className={`changes-run-head team-ledger__head ${isOpen ? "changes-run-head--open" : "changes-run-head--folded"}`}
                      role="button" tabIndex={row ? -1 : 0} aria-expanded={isOpen} aria-label={p.label}
                      onClick={() => toggle(p.pluginId)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          toggle(p.pluginId);
                        } else if (e.key === "ArrowRight") {
                          e.preventDefault();
                          setDetail(p.pluginId);
                        }
                      }}>
                      <span className="changes-run-chevron" aria-hidden>{isOpen ? <Icons.ChevronDown /> : <Icons.ChevronRight />}</span>
                      <TeamPluginIcon row={p} />
                      <span className="team-ledger__title">
                        <span className="changes-run-name">{p.label}</span>
                        <span className="changes-run-meta">{meta(p, section)}</span>
                      </span>
                      {b.text ? <span className={`changeset-badge${b.tone ? ` team-ledger__badge--${b.tone}` : ""}`}>{b.text}</span> : null}
                      <span className="changes-run-actions" onClick={(e) => e.stopPropagation()}>
                        <button type="button" className="changeset-btn changeset-btn--icon" tabIndex={row ? -1 : 0}
                          aria-label={`Open ${p.label}`} title={`Open ${p.label}`} onClick={() => setDetail(p.pluginId)}>
                          <Icons.ChevronRight />
                        </button>
                      </span>
                    </div>
                    {isOpen ? <Preview row={p} section={section} onOpen={() => setDetail(p.pluginId)} /> : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="plugin-scope-empty">No plugin shows {team.label}'s data yet.</p>
          )}
        </div>
        <div className="team-slide__panel team-slide__panel--detail" aria-hidden={row ? undefined : true}>
          {row ? <PluginDetail key={row.pluginId} row={row} team={team} section={section} onBack={back} onSection={onSection} /> : null}
        </div>
      </div>
    </div>
  );
}
