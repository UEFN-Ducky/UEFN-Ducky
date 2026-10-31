import { useEffect, useRef, useState } from "react";
import "./plugin-ui.css";
import { Modal } from "../components/Modal";
import { getApi } from "../hooks/usePanelApi";
import type { PanelApi } from "../types/panel";
import { readActiveTeam, refreshActiveTeam, useActiveTeam, type ActiveTeam } from "./activeTeamStore";
import { PaneCard } from "./PaneCard";
import type { ScopeTab } from "./scopeBarText";
import { TeamPicker } from "./TeamPicker";
import { TeamPlugins } from "./TeamPlugins";
import { useTeamScope, type TeamScope } from "./teamScopeStore";

export type TeamSection = "overview" | ScopeTab;

type Props = {
  open: boolean;
  onClose: () => void;
  /** The section shown when it opens. */
  initial?: TeamSection;
};

/** The host's team timing settings (sync_scheduler clamps them; app-wide, every team copy). */
type TimingKey = "team_send_delay_s" | "team_poll_s";
const SLIDERS: { key: TimingKey; label: string; min: number; max: number; step: number; fallback: number }[] = [
  { key: "team_send_delay_s", label: "Send changes after", min: 0, max: 60, step: 5, fallback: 10 },
  { key: "team_poll_s", label: "Check for teammates' changes every", min: 30, max: 600, step: 30, fallback: 60 },
];
/** A drag saves once it settles, not on every step. */
const SAVE_SETTLE_MS = 400;

/** "0 s", "45 s", "1 min 30 s", "10 min". */
function secondsText(s: number): string {
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
}

/** Settings' sliders: how long changes collect before they go out together, and how often
 * teammates' changes are looked for. */
function TimingSliders() {
  const [values, setValues] = useState<Record<TimingKey, number> | null>(null);
  const pending = useRef<Partial<Record<TimingKey, number>>>({});
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    let live = true;
    void Promise.resolve(getApi()?.get_settings?.()).then((s) => {
      if (!live) return;
      const read = (s ?? {}) as Partial<Record<TimingKey, number>>;
      setValues({
        team_send_delay_s: read.team_send_delay_s ?? SLIDERS[0].fallback,
        team_poll_s: read.team_poll_s ?? SLIDERS[1].fallback,
      });
    }).catch(() => undefined);
    return () => {
      live = false;
      window.clearTimeout(timer.current);
    };
  }, []);
  if (!values) return null;

  const change = (key: TimingKey, value: number) => {
    setValues({ ...values, [key]: value });
    pending.current[key] = value;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const patch = pending.current;
      pending.current = {};
      void getApi()?.save_agent_settings?.(patch as unknown as Parameters<PanelApi["save_agent_settings"]>[0]);
    }, SAVE_SETTLE_MS);
  };

  return (
    <div className="team-modal-sliders">
      <div className="plugin-scope-subhead">Timing (every team copy)</div>
      {SLIDERS.map((s) => (
        <label key={s.key} className="appearance-sounds-row">
          <span>{s.label}</span>
          <input type="range" min={s.min} max={s.max} step={s.step} value={values[s.key]}
            onChange={(e) => change(s.key, Number(e.target.value))} />
          <span className="appearance-sounds-vol">{secondsText(values[s.key])}</span>
        </label>
      ))}
    </div>
  );
}

/** "!" / "✓" / "•" before the team's status phrase. */
function phraseIcon(team: ActiveTeam): string {
  if (team.tone) return "!";
  return team.phrase === "Up to date" ? "✓" : "•";
}

/** Overview: the team's status and, while a plugin tab is on screen, that plugin's own card. */
function Overview({ team, pane, onSection, onPaneSection }: {
  team: ActiveTeam; pane: TeamScope | null; onSection: (s: TeamSection) => void; onPaneSection: (s: ScopeTab) => void;
}) {
  return (
    <div className="team-modal-overview">
      <div className="team-modal-status">
        <span className={`plugin-scope-status${team.tone ? ` plugin-scope-status--${team.tone}` : ""}`}>
          <span className="plugin-scope-status__icon" aria-hidden>{phraseIcon(team)}</span>
          <span className="plugin-scope-btn__label">{team.shared ? `${team.label}: ${team.phrase}` : team.phrase}</span>
        </span>
        <span className="team-modal-status__actions">
          {team.conflicts ? (
            <button type="button" className="plugin-scope-btn plugin-scope-btn--primary" onClick={() => onSection("conflicts")}>
              Resolve
            </button>
          ) : team.incoming ? (
            <button type="button" className="plugin-scope-btn" onClick={() => onSection("incoming")}>See incoming</button>
          ) : null}
        </span>
      </div>
      {team.shared ? null : (
        <p className="plugin-scope-empty">Everything is on this PC. Pick a team under Your team to work with others.</p>
      )}
      {pane ? <PaneCard scope={pane} onSection={onPaneSection} /> : null}
    </div>
  );
}

const TEAM_TABS: { id: ScopeTab; label: string }[] = [
  { id: "incoming", label: "Incoming" },
  { id: "changes", label: "Changes" },
  { id: "conflicts", label: "Conflicts" },
  { id: "locks", label: "Locks" },
  { id: "history", label: "History" },
  { id: "people", label: "People" },
  { id: "settings", label: "Settings" },
];

/**
 * The header's Team menu, the same on every tab (a chat, a plugin, Settings): Ducky's modal with
 * Settings' left nav. On top of the nav, Your team picks the team this PC works in, or Local
 * ({@link TeamPicker}). Then Overview (the team's status; on a plugin tab that plugin's own card),
 * and on a team Incoming, Changes, Conflicts, Locks, History, People and Settings: each lists the
 * team's plugins as Ledger groups, and one opens by sliding in ({@link TeamPlugins}). Settings
 * also has the timing sliders. It reads the active team ({@link useActiveTeam}); the plugin on
 * screen only adds its own card and row.
 */
export function TeamModal({ open, onClose, initial = "overview" }: Props) {
  const team = useActiveTeam();
  const pane = useTeamScope();
  const [section, setSection] = useState<TeamSection>(initial);
  // The plugin a status press asked for: its view slides in on that section.
  const [focus, setFocus] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setSection(initial);
    setFocus(initial !== "overview" && pane ? pane.pluginId : null);
    void readActiveTeam();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial]);
  // The plugin on screen moved to another copy: the team may have moved with it.
  useEffect(() => {
    if (open) refreshActiveTeam();
  }, [open, pane?.current]);

  const shared = Boolean(team?.shared || pane?.shared);
  const counts: Partial<Record<ScopeTab, { count: number; alert?: boolean }>> = team ? {
    incoming: { count: team.incoming + team.blocked, alert: team.blocked > 0 },
    changes: { count: team.pending },
    conflicts: { count: Math.max(team.conflicts, pane?.conflicts ?? 0), alert: Math.max(team.conflicts, pane?.conflicts ?? 0) > 0 },
  } : {};
  const tabs = shared ? TEAM_TABS : TEAM_TABS.filter((t) => t.id === "settings");
  const shown: TeamSection = section === "overview" || tabs.some((t) => t.id === section) ? section : "overview";
  const notes = pane?.notes ?? [];
  const overviewAlert = notes.some((n) => n.tone === "error");
  const title = `${team?.shared ? team.label : team ? "Local" : "Team"} · Team settings`;
  const paneSection = (s: ScopeTab) => {
    setSection(s);
    setFocus(pane?.pluginId ?? null);
  };
  const plugins = team ? <TeamPlugins team={team} section={shown} onSection={setSection} openId={focus}
    onOpened={() => setFocus(null)} /> : null;

  return (
    <Modal open={open} onClose={onClose} title={title} width={900} className="team-modal" bodyClassName="team-modal-body">
      <div className="team-modal-layout">
        <nav className="settings-view-sidebar-nav team-modal-nav" aria-label="Team">
          <TeamPicker team={team} />
          {[{ id: "overview" as const, label: "Overview", count: notes.length, alert: overviewAlert }, ...tabs.map((t) => ({ ...t, ...counts[t.id] }))].map((t) => (
            <button key={t.id} type="button" aria-current={shown === t.id ? "page" : undefined}
              className={`settings-view-sidebar-tab${shown === t.id ? " is-active" : ""}`} onClick={() => setSection(t.id)}>
              <span>{t.label}</span>
              {t.count ? <span className={`plugin-scope-count${t.alert ? " is-alert" : ""}`}>{t.count}</span> : null}
            </button>
          ))}
        </nav>
        <div className="team-modal-pane">
          {!team ? (
            <div className="team-modal-overview"><p className="plugin-scope-empty">Loading…</p></div>
          ) : shown === "overview" ? (
            <>
              <Overview team={team} pane={pane} onSection={setSection} onPaneSection={paneSection} />
              {team.shared ? plugins : null}
            </>
          ) : (
            <>
              {shown === "settings" ? <TimingSliders /> : null}
              {shared ? plugins : null}
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
