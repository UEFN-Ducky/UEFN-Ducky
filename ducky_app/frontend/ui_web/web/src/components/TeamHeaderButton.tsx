import { useState } from "react";
import { Icons } from "../icons/Icons";
import { TeamModal } from "../plugin-ui/TeamModal";
import { useTeamScope } from "../plugin-ui/teamScopeStore";

/**
 * Team in the title bar, next to Live: the visible plugin pane's team copy (or Local), a dot for
 * its state and the count of what needs looking at (to send, coming in, conflicts; red with a
 * conflict). Opens the Team modal. Hidden while no plugin pane that can share is on screen.
 */
export function TeamHeaderButton() {
  const scope = useTeamScope();
  const [open, setOpen] = useState(false);
  if (!scope) return null;
  const label = scope.shared ? scope.status.scope?.label || "Team" : "Local";
  const tone = scope.notes.some((n) => n.tone === "error") ? "error" : scope.phrase.tone;
  const dot = tone || (scope.shared ? scope.variant : "local");
  const title = `${label} · ${scope.syncing ? "Syncing…" : scope.phrase.text}`;
  return (
    <>
      <button type="button" className="icon-btn app-header-team-btn" title={title} aria-label={`Team: ${title}`}
        aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <Icons.Users />
        <span className={`plugin-scope-dot plugin-scope-dot--${dot} app-header-team-btn__dot`} aria-hidden />
        {scope.badge ? (
          <span className={`plugin-scope-count app-header-team-btn__count${scope.conflicts ? " is-alert" : ""}`}>{scope.badge}</span>
        ) : null}
      </button>
      <TeamModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
