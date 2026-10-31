import { useEffect, useRef } from "react";
import { phraseTab, type ScopeTab } from "./scopeBarText";
import { ScopePicker } from "./ScopePicker";
import type { TeamScope } from "./teamScopeStore";

/** "!" / "↻" / "✓" / "•" before the status phrase. */
function phraseIcon(scope: TeamScope): string {
  const { phrase, syncing } = scope;
  if (phrase.tone) return "!";
  if (syncing) return "↻";
  return phrase.key === "up_to_date" ? "✓" : "•";
}

/**
 * The plugin on screen's own copy: its status (it opens the section that explains it), Update
 * now / Send / Get / Retry, the notes that need a decision (reset, paused, a refused write), and
 * whose data it shows. Its pane's own callbacks do the work. In the Team modal's Overview while a
 * plugin tab is on screen, and in that plugin's slid-in view.
 */
export function PaneCard({ scope, onSection }: { scope: TeamScope; onSection: (s: ScopeTab) => void }) {
  const { status, phrase, actions, shared, busy, refusal } = scope;
  const label = status.scope?.label || "Local";
  // The teams are read once per open (the actions object is new every render).
  const load = useRef(actions.loadChoices);
  useEffect(() => {
    void load.current();
  }, []);

  return (
    <div className="team-pane-card" aria-label={`${scope.plugin} on this tab`} role="group">
      <div className="plugin-scope-subhead">{scope.plugin} · on this tab</div>
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
