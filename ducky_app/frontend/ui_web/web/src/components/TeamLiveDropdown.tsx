import { useEffect, useRef, useState } from "react";
import { readLiveOverview, useLiveOverview } from "../hooks/useLiveOverview";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import { LiveMenu } from "../plugin-ui/LiveMenu";
import { useLiveFollow, writeLiveFollow } from "../plugin-ui/liveFollow";
import { OPEN_TEAM_MODAL_EVENT } from "../plugin-ui/scopeActivity";
import { groupName } from "../plugin-ui/scopeBarText";
import { readActiveTeam, useActiveTeam } from "../plugin-ui/activeTeamStore";
import { TeamModal, type TeamSection } from "../plugin-ui/TeamModal";
import type { LiveTeam, LiveTeamOnline } from "../types/panel";
import { DropdownPanel } from "./DropdownPanel";
import { LiveInvitePrompt } from "./LiveInvitePrompt";

/**
 * Team and Live in the title bar, one button: green while this PC is live (with how many are
 * in its group), pulsing while it links to someone, a dot for the team this PC works in (amber
 * when someone live can't be reached or needs to update, red on an error) and the count of what
 * needs looking at across the team's plugins (red with a conflict). The same on every tab: it
 * reads the active team, never the plugin pane on screen. The menu has the live groups and people
 * per team; Settings (always there) opens the Team modal, as does the activity tray
 * (`OPEN_TEAM_MODAL_EVENT`). Always shown, on the phone too (every call it makes is allowed off the PC).
 */
export function TeamLiveDropdown() {
  const overview = useLiveOverview();
  const team = useActiveTeam();
  const follow = useLiveFollow();
  const [open, setOpen] = useState(false);
  const [modal, setModal] = useState<TeamSection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const anchorRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    void readLiveOverview(); // fresh when opened: who is live may have moved since
    void readActiveTeam();
  }, [open]);
  useEffect(() => {
    const onOpen = (event: Event) => {
      setOpen(false);
      setModal((event as CustomEvent<{ section?: TeamSection }>).detail?.section || "overview");
    };
    window.addEventListener(OPEN_TEAM_MODAL_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_TEAM_MODAL_EVENT, onOpen);
  }, []);

  const teams = overview?.teams ?? [];
  const live = teams.find((t) => t.on);
  const mine = live?.groups.find((g) => g.mine);
  const others = mine ? mine.members.filter((m) => !m.me) : [];
  const connecting = Boolean(live?.connecting?.length);
  const warn = Boolean(live && ((live.unlinked ?? []).length || others.some((m) => m.older || m.partial)));
  const groupsLive = teams.reduce((n, t) => n + t.groups.filter((g) => !g.mine).length, 0);
  const liveTitle = live && mine
    ? `${mine.hostIsMe ? "You're live" : `Live with ${groupName(mine)}`} in ${live.label} · ${mine.members.length} ${mine.members.length === 1 ? "person" : "people"}`
    : groupsLive
      ? `${groupsLive} live ${groupsLive === 1 ? "group" : "groups"} on your team`
      : "";
  const teamTitle = team ? `${team.shared ? team.label : "Local"} · ${team.phrase}` : "";
  const title = [teamTitle, liveTitle].filter(Boolean).join("\n") || "Team and Live";
  const tone = team?.tone === "error" ? "error" : warn ? "warn" : team?.tone;
  const dot = tone || (team ? (team.shared ? "shared" : "local") : "");

  const act = async (run: () => Promise<{ ok?: boolean; error?: string } | undefined>, keepOpen = false) => {
    setBusy(true);
    setError("");
    try {
      const out = await run();
      if (out && out.ok === false) setError(out.error || "That didn't work.");
      else if (!keepOpen) setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      void readLiveOverview();
    }
  };
  const join = (team: LiveTeam, room?: string) =>
    void act(async () => getApi()?.live_team_join?.(team.account, team.space, room || ""));
  const leave = (team: LiveTeam) => void act(async () => getApi()?.live_team_leave?.(team.account, team.space));
  // The menu stays open: the row turns to Invited, and more people can be asked.
  const invite = (team: LiveTeam, person: LiveTeamOnline) =>
    void act(async () => getApi()?.live_team_invite?.(team.account, team.space, person.device), true);
  // Every plugin moves to that team; each leaves its old team's live group at once.
  const use = (picked: LiveTeam) => void act(async () => {
    const out = await getApi()?.plugin_scope_set_all?.(picked.space);
    await readActiveTeam();
    return out?.errors?.length ? { ok: false, error: out.errors.map((e) => e.error).join(" ") } : out;
  });
  const settings = () => {
    setOpen(false);
    setModal("overview");
  };

  return (
    <div className="connection-status-root no-drag">
      <LiveInvitePrompt />
      <button
        ref={anchorRef}
        type="button"
        className={`no-drag connection-status-btn live-trigger app-header-team-btn${open ? " is-active" : ""}${live ? " is-live" : ""}${connecting ? " is-connecting" : ""}`}
        title={title}
        aria-label="Team and Live"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Users />
        {mine && mine.members.length > 1 ? <span className="live-trigger-count">{mine.members.length}</span> : null}
        {dot ? <span className={`plugin-scope-dot plugin-scope-dot--${dot} app-header-team-btn__dot`} aria-hidden /> : null}
        {team?.badge ? (
          <span className={`plugin-scope-count app-header-team-btn__count${team.conflicts ? " is-alert" : ""}`}>{team.badge}</span>
        ) : null}
      </button>
      <DropdownPanel anchorRef={anchorRef} open={open} onClose={() => setOpen(false)} width={320} placement="bottom">
        {error ? <p className="plugin-scope-menu__note plugin-scope-menu__note--warn live-menu__error" role="alert">{error}</p> : null}
        {teams.length ? (
          <LiveMenu teams={teams} busy={busy} onJoin={join} onLeave={leave} onInvite={invite} follow={follow} onFollow={writeLiveFollow}
            current={team?.current} onUse={getApi()?.plugin_scope_set_all ? use : undefined} />
        ) : <div className="plugin-scope-menu__empty">No team yet.</div>}
        <div className="plugin-scope-menu__foot team-live-menu__foot">
          <span className="plugin-scope-menu__sub team-live-menu__status">{teamTitle}</span>
          <button type="button" className="plugin-scope-btn plugin-scope-btn--row" onClick={settings}>Settings</button>
        </div>
      </DropdownPanel>
      <TeamModal open={modal !== null} initial={modal ?? "overview"} onClose={() => setModal(null)} />
    </div>
  );
}
