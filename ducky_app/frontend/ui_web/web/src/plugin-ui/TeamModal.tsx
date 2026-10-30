import { useEffect, useRef, useState } from "react";
import "./plugin-ui.css";
import { Modal } from "../components/Modal";
import { getApi } from "../hooks/usePanelApi";
import type { PanelApi } from "../types/panel";
import { PluginScopeDrawer, scopeTabs } from "./PluginScopeDrawer";
import { phraseTab, type ScopeTab } from "./scopeBarText";
import { ScopePicker } from "./ScopePicker";
import { useTeamScopeOrLast, type TeamScope } from "./teamScopeStore";

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

/** "!" / "↻" / "✓" / "•" before the status phrase. */
function phraseIcon(scope: TeamScope): string {
  const { phrase, syncing } = scope;
  if (phrase.tone) return "!";
  if (syncing) return "↻";
  return phrase.key === "up_to_date" ? "✓" : "•";
}

/** Overview: whose data the plugin shows (the team picker), its status and buttons, and the
 * notes that need a decision. */
function Overview({ scope, onSection }: { scope: TeamScope; onSection: (s: TeamSection) => void }) {
  const { status, phrase, actions, shared, busy, refusal } = scope;
  const label = status.scope?.label || "Local";
  // The teams are read once per open (the actions object is new every render).
  const load = useRef(actions.loadChoices);
  useEffect(() => {
    void load.current();
  }, []);

  return (
    <div className="team-modal-overview">
      <div className="team-modal-status">
        <button type="button" className={`plugin-scope-btn plugin-scope-btn--ghost plugin-scope-status${phrase.tone ? ` plugin-scope-status--${phrase.tone}` : ""}`}
          aria-live="polite" title={phrase.text} disabled={!shared && !refusal}
          onClick={() => onSection(phraseTab(phrase.key))}>
          <span className="plugin-scope-status__icon" aria-hidden>{phraseIcon(scope)}</span>
          <span className="plugin-scope-btn__label">{scope.syncing ? "Syncing…" : phrase.text}</span>
        </button>
        <span className="team-modal-status__actions">
          {scope.canSync && scope.manual ? (
            <>
              {scope.canPush ? (
                <button type="button" className="plugin-scope-btn" disabled={busy} onClick={() => void actions.push()}
                  title={`Send this PC's changes to ${label} now`}>Send</button>
              ) : null}
              {scope.canPull ? (
                <button type="button" className="plugin-scope-btn" disabled={busy} onClick={() => void actions.pull()}
                  title={`Get everyone else's changes from ${label} now`}>Get</button>
              ) : null}
            </>
          ) : phrase.action === "update" ? (
            <button type="button" className="plugin-scope-btn plugin-scope-btn--primary" disabled={busy} onClick={() => void actions.pull()}>
              Update now
            </button>
          ) : scope.canSync && scope.failing ? (
            <button type="button" className="plugin-scope-btn" disabled={scope.syncing} onClick={() => void actions.syncNow()}>
              Retry
            </button>
          ) : null}
        </span>
      </div>
      {scope.notes.map((n) => (
        <div key={n.key} className={`plugin-scope-note${n.tone ? ` plugin-scope-note--${n.tone}` : ""}`}>
          <span>{n.text}</span>
          {n.actions?.map((a) => (
            <button key={a.label} type="button" className={`plugin-scope-btn${a.ghost ? " plugin-scope-btn--ghost" : ""}`}
              disabled={a.disabled} onClick={a.run}>{a.label}</button>
          ))}
        </div>
      ))}
      <div className="plugin-scope-subhead">Whose data {scope.plugin} shows</div>
      <ScopePicker scope={scope} />
    </div>
  );
}

/**
 * The header's Team menu, in Ducky's modal look with Settings' left nav: Overview (whose data,
 * status, Update now / Send / Get / Retry, notes), Settings (timing; on a team copy also the
 * copy's own settings) and, on a team copy, every Source control section (Changes, Incoming,
 * Conflicts, Locks, History, People). It acts on the visible plugin pane's copy, or the last one
 * shown while that pane is hidden ({@link useTeamScopeOrLast}); the pane's own callbacks do the work.
 */
export function TeamModal({ open, onClose, initial = "overview" }: Props) {
  const scope = useTeamScopeOrLast();
  const [section, setSection] = useState<TeamSection>(initial);
  useEffect(() => {
    if (open) setSection(initial);
  }, [open, initial]);

  const shared = Boolean(scope?.shared);
  const label = scope ? (shared ? scope.status.scope?.label || "Team" : "Local") : "";
  const tabs: { id: TeamSection; label: string; count?: number; alert?: boolean }[] =
    scope && shared ? scopeTabs(scope.status, scope.pluginId) : [{ id: "settings", label: "Settings" }];
  const shown: TeamSection = section === "overview" || tabs.some((t) => t.id === section) ? section : "overview";
  const notes = scope?.notes ?? [];
  const overviewAlert = notes.some((n) => n.tone === "error");

  return (
    <Modal open={open} onClose={onClose} title={scope ? `${label} · ${scope.plugin}` : "Team"} width={860}
      className="team-modal" bodyClassName="team-modal-body">
      <div className="team-modal-layout">
        <nav className="settings-view-sidebar-nav team-modal-nav" aria-label="Team">
          {[{ id: "overview" as const, label: "Overview", count: notes.length, alert: overviewAlert }, ...tabs].map((t) => (
            <button key={t.id} type="button" aria-current={shown === t.id ? "page" : undefined}
              className={`settings-view-sidebar-tab${shown === t.id ? " is-active" : ""}`} onClick={() => setSection(t.id)}>
              <span>{t.label}</span>
              {t.count ? <span className={`plugin-scope-count${t.alert ? " is-alert" : ""}`}>{t.count}</span> : null}
            </button>
          ))}
        </nav>
        <div className="team-modal-pane">
          {shown === "overview" ? (
            scope ? (
              <Overview scope={scope} onSection={setSection} />
            ) : (
              <div className="team-modal-overview">
                <p className="plugin-scope-empty">Open a plugin that shares to see whose data it shows and its changes.</p>
              </div>
            )
          ) : (
            <>
              {shown === "settings" ? <TimingSliders /> : null}
              {scope && shared ? (
                <PluginScopeDrawer pluginId={scope.pluginId} status={scope.status} tab={shown} onTab={setSection} hideTabs
                  onJoin={scope.actions.joinLive ? (room) => void scope.actions.joinLive?.(room) : undefined}
                  onChanged={(changed) => scope.actions.refresh(changed)} />
              ) : null}
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
