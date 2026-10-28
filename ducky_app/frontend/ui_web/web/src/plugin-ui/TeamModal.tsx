import { useEffect, useRef, useState } from "react";
import "./plugin-ui.css";
import { Modal } from "../components/Modal";
import { PluginScopeDrawer, scopeTabs } from "./PluginScopeDrawer";
import { kindTitle, phraseTab, type ScopeTab } from "./scopeBarText";
import { useTeamScope, type TeamScope } from "./teamScopeStore";

export type TeamSection = "overview" | ScopeTab;

type Props = {
  open: boolean;
  onClose: () => void;
  /** The section shown when it opens. */
  initial?: TeamSection;
};

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
  const { status, phrase, actions, shared, busy, menu, refusal, current } = scope;
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
      {!status.canChange ? (
        <p className="plugin-scope-empty">Open a project to choose whose data it shows.</p>
      ) : menu === null ? (
        <p className="plugin-scope-empty">Loading…</p>
      ) : (
        <div className="plugin-scope-menu" role="radiogroup" aria-label="Whose data">
          {/* A folder that can't be shared from this PC: no team to offer, and why. */}
          {refusal && !shared ? <div className="plugin-scope-menu__note plugin-scope-menu__note--warn">{refusal}</div> : null}
          {menu.filter((choice) => !(refusal && !shared && choice.kind === "shared")).map((choice) => (
            <button key={choice.id} type="button" role="radio" aria-checked={choice.id === current} disabled={scope.switching}
              className="plugin-scope-menu__row plugin-scope-menu__choice" onClick={() => void actions.choose(choice)}>
              <span className={`plugin-scope-dot plugin-scope-dot--${choice.kind === "shared" ? "shared" : "local"}`} aria-hidden />
              <span className="plugin-scope-menu__text">
                <span className="plugin-scope-menu__name">{choice.kind === "shared" ? choice.label : "Local"}</span>
                <span className="plugin-scope-menu__sub">{choice.kind === "shared" ? `${kindTitle(scope.kind)} copy` : "Only on this PC"}</span>
              </span>
              {choice.id === current ? <span className="plugin-scope-menu__check" aria-hidden>✓</span> : null}
            </button>
          ))}
          {scope.offers.map((offer) => (
            <button key={offer.space} type="button" className="plugin-scope-btn plugin-scope-btn--block" disabled={busy}
              onClick={() => void actions.copyInto(offer)}>
              Copy Local into {offer.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The header's Team menu, in Ducky's modal look with Settings' left nav: Overview (whose data,
 * status, Update now / Send / Get / Retry, notes) and, on a team copy, every Source control
 * section (Changes, Incoming, Conflicts, Locks, History, People, Settings). It shows and acts on
 * the visible plugin pane's copy ({@link useTeamScope}); the pane's own callbacks do the work.
 */
export function TeamModal({ open, onClose, initial = "overview" }: Props) {
  const scope = useTeamScope();
  const [section, setSection] = useState<TeamSection>(initial);
  useEffect(() => {
    if (open) setSection(initial);
  }, [open, initial]);

  if (!scope) return null;
  const { status, shared, actions } = scope;
  const label = shared ? status.scope?.label || "Team" : "Local";
  const tabs = shared ? scopeTabs(status, scope.pluginId) : [];
  const shown: TeamSection = section === "overview" || tabs.some((t) => t.id === section) ? section : "overview";
  const overviewAlert = scope.notes.some((n) => n.tone === "error");

  return (
    <Modal open={open} onClose={onClose} title={`${label} · ${scope.plugin}`} width={860}
      className="team-modal" bodyClassName="team-modal-body">
      <div className="team-modal-layout">
        <nav className="settings-view-sidebar-nav team-modal-nav" aria-label="Team">
          {[{ id: "overview" as const, label: "Overview", count: scope.notes.length, alert: overviewAlert }, ...tabs].map((t) => (
            <button key={t.id} type="button" aria-current={shown === t.id ? "page" : undefined}
              className={`settings-view-sidebar-tab${shown === t.id ? " is-active" : ""}`} onClick={() => setSection(t.id)}>
              <span>{t.label}</span>
              {t.count ? <span className={`plugin-scope-count${t.alert ? " is-alert" : ""}`}>{t.count}</span> : null}
            </button>
          ))}
        </nav>
        <div className="team-modal-pane">
          {shown === "overview" ? (
            <Overview scope={scope} onSection={setSection} />
          ) : (
            <PluginScopeDrawer pluginId={scope.pluginId} status={status} tab={shown} onTab={setSection} hideTabs
              onJoin={actions.joinLive ? (room) => void actions.joinLive?.(room) : undefined}
              onChanged={(changed) => actions.refresh(changed)} />
          )}
        </div>
      </div>
    </Modal>
  );
}
