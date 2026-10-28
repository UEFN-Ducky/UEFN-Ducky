import { useEffect, useState } from "react";
import { Icons } from "../icons/Icons";
import { OPEN_TEAM_MODAL_EVENT } from "../plugin-ui/scopeActivity";
import { TeamModal, type TeamSection } from "../plugin-ui/TeamModal";
import { useTeamScope } from "../plugin-ui/teamScopeStore";

/**
 * Team in the title bar, next to Live: the visible plugin pane's team copy (or Local), a dot for
 * its state and the count of what needs looking at (to send, coming in, conflicts; red with a
 * conflict). Opens the Team modal, also when the activity tray asks (`OPEN_TEAM_MODAL_EVENT`,
 * its conflict row's Resolve). Hidden while no plugin pane that can share is on screen.
 */
export function TeamHeaderButton() {
  const scope = useTeamScope();
  const [open, setOpen] = useState<TeamSection | null>(null);
  useEffect(() => {
    const onOpen = (event: Event) => {
      setOpen((event as CustomEvent<{ section?: TeamSection }>).detail?.section || "overview");
    };
    window.addEventListener(OPEN_TEAM_MODAL_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_TEAM_MODAL_EVENT, onOpen);
  }, []);
  if (!scope) return null;
  const label = scope.shared ? scope.status.scope?.label || "Team" : "Local";
  const tone = scope.notes.some((n) => n.tone === "error") ? "error" : scope.phrase.tone;
  const dot = tone || (scope.shared ? scope.variant : "local");
  const title = `${label} · ${scope.syncing ? "Syncing…" : scope.phrase.text}`;
  return (
    <>
      <button type="button" className="icon-btn app-header-team-btn" title={title} aria-label={`Team: ${title}`}
        aria-haspopup="dialog" onClick={() => setOpen("overview")}>
        <Icons.Users />
        <span className={`plugin-scope-dot plugin-scope-dot--${dot} app-header-team-btn__dot`} aria-hidden />
        {scope.badge ? (
          <span className={`plugin-scope-count app-header-team-btn__count${scope.conflicts ? " is-alert" : ""}`}>{scope.badge}</span>
        ) : null}
      </button>
      <TeamModal open={open !== null} initial={open ?? "overview"} onClose={() => setOpen(null)} />
    </>
  );
}
