import { useEffect, useRef, useState } from "react";
import { readLiveOverview, useLiveOverview } from "../hooks/useLiveOverview";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import { LiveMenu } from "../plugin-ui/LiveMenu";
import { useLiveFollow, writeLiveFollow } from "../plugin-ui/liveFollow";
import { groupName } from "../plugin-ui/scopeBarText";
import type { LiveTeam, LiveTeamOnline } from "../types/panel";
import { DropdownPanel } from "./DropdownPanel";
import { LiveInvitePrompt } from "./LiveInvitePrompt";

/**
 * Live in the title bar, next to the activity list: grey while this PC isn't live, green
 * while it is (with how many are in its group), pulsing while it links to someone, and a
 * corner dot when someone in the group can't be reached or needs to update. The menu starts
 * or joins a group on a team copy, every plugin of it with it, and leaves it. Not on the
 * phone, and not while no plugin here is on a team copy.
 */
export function LiveHeaderDropdown() {
  const overview = useLiveOverview();
  const follow = useLiveFollow();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const anchorRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) void readLiveOverview(); // fresh when opened: who is live may have moved since
  }, [open]);

  const teams = overview?.teams ?? [];
  if (isRemote()) return null;
  if (!teams.length) return <LiveInvitePrompt />;
  const live = teams.find((t) => t.on);
  const mine = live?.groups.find((g) => g.mine);
  const others = mine ? mine.members.filter((m) => !m.me) : [];
  const connecting = Boolean(live?.connecting?.length);
  const warn = Boolean(live && ((live.unlinked ?? []).length || others.some((m) => m.older || m.partial)));
  const groupsLive = teams.reduce((n, t) => n + t.groups.filter((g) => !g.mine).length, 0);
  const title = live && mine
    ? `${mine.hostIsMe ? "You're live" : `Live with ${groupName(mine)}`} in ${live.label} · ${mine.members.length} ${mine.members.length === 1 ? "person" : "people"}`
    : groupsLive
      ? `Live: ${groupsLive} ${groupsLive === 1 ? "group" : "groups"} on your team. Join one, or start live.`
      : "Live: work on the team's data together, seeing each other's pointers";

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

  return (
    <div className="connection-status-root no-drag">
      <LiveInvitePrompt />
      <button
        ref={anchorRef}
        type="button"
        className={`no-drag connection-status-btn live-trigger${open ? " is-active" : ""}${live ? " is-live" : ""}${connecting ? " is-connecting" : ""}`}
        title={title}
        aria-label="Live"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Live />
        {mine && mine.members.length > 1 ? <span className="live-trigger-count">{mine.members.length}</span> : null}
        {warn ? <span className="store-update-dot live-trigger-dot" aria-hidden /> : null}
      </button>
      <DropdownPanel anchorRef={anchorRef} open={open} onClose={() => setOpen(false)} width={320} placement="bottom">
        {error ? <p className="plugin-scope-menu__note plugin-scope-menu__note--warn live-menu__error" role="alert">{error}</p> : null}
        <LiveMenu teams={teams} busy={busy} onJoin={join} onLeave={leave} onInvite={invite} follow={follow} onFollow={writeLiveFollow} />
      </DropdownPanel>
    </div>
  );
}
