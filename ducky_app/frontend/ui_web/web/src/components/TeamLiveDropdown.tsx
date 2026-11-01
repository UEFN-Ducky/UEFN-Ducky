import { useEffect, useRef, useState } from "react";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { readLiveOverview, useLiveOverview } from "../hooks/useLiveOverview";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import { LiveMenu } from "../plugin-ui/LiveMenu";
import { useLiveFollow, writeLiveFollow } from "../plugin-ui/liveFollow";
import { OPEN_TEAM_MODAL_EVENT } from "../plugin-ui/scopeActivity";
import { groupName } from "../plugin-ui/scopeBarText";
import { copyDot, readActiveTeam, useActiveTeam, type ActiveTeam, type CopyDot } from "../plugin-ui/activeTeamStore";
import { TeamModal, type TeamSection } from "../plugin-ui/TeamModal";
import type { LiveTeam, LiveTeamOnline, PluginScopeChoice } from "../types/panel";
import { DropdownPanel } from "./DropdownPanel";
import { LiveInvitePrompt } from "./LiveInvitePrompt";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const LOCAL: PluginScopeChoice = { id: "personal", kind: "personal", label: "Local" };
const DOT_TITLE: Record<CopyDot, string> = {
  error: "Conflicts or an error to look at", warn: "Something is waiting to sync", ok: "In sync", local: "This PC only",
};

function Dot({ dot, className = "" }: { dot: CopyDot; className?: string }) {
  return <span className={`plugin-scope-dot plugin-scope-dot--${dot}${className ? ` ${className}` : ""}`} title={DOT_TITLE[dot]}
    data-dot={dot} aria-hidden />;
}

/** The dot of one row of the copy picker: the copy this PC is on reads the active team. */
function rowDot(c: PluginScopeChoice, team: ActiveTeam | null): CopyDot {
  if (c.kind !== "shared") return "local";
  return team?.current === c.id ? team.dot : copyDot(c.state, c.pending ?? 0);
}

/**
 * Which copy the whole app is on: Local or one team. A custom dropdown (never a native select): the
 * current copy with its dot, and when opened Local and every team of the account, the current one
 * checked. Picking another asks first, then switches every plugin, Workflows and General storage
 * together (`plugin_scope_set_all`).
 */
function CopyPicker({ team, onSwitched }: { team: ActiveTeam | null; onSwitched: () => void }) {
  const { confirm } = useConfirmModal();
  const [listOpen, setListOpen] = useState(false);
  const [choices, setChoices] = useState<PluginScopeChoice[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    let live = true;
    void Promise.resolve(getApi()?.plugin_scope_choices?.()).then((got) => {
      if (live) setChoices(got?.choices?.length ? got.choices : [LOCAL]);
    }).catch(() => {
      if (live) setChoices([LOCAL]);
    });
    return () => {
      live = false;
    };
  }, []);

  const current = team?.current ?? "personal";
  const shared = Boolean(team?.shared);
  const dot: CopyDot = team ? team.dot : "local";

  const pick = async (c: PluginScopeChoice) => {
    setListOpen(false);
    if (c.id === current) return;
    const name = c.kind === "shared" ? c.label : "Local";
    const ok = await confirm({
      title: c.kind === "shared" ? `Switch everything to ${name}?` : "Switch everything to Local?",
      message: [
        c.kind === "shared"
          ? `Every plugin, Workflows and General storage show ${name}'s data.`
          : "Every plugin, Workflows and General storage show their Local data, only on this PC.",
        shared && team ? `You leave ${team.label}'s live group now: teammates there stop seeing you at once.` : "",
        "Nothing is copied.",
      ].filter(Boolean).join(" "),
      confirmLabel: "Switch",
    });
    if (ok !== true) return;
    setBusy(true);
    setError("");
    setNote("");
    try {
      const out = await getApi()?.plugin_scope_set_all?.(c.id);
      if (!out) setError("This app can't switch teams yet.");
      else if (out.errors?.length) setError(out.errors.map((e) => `${e.plugin}: ${e.error}`).join(" "));
      else if (out.error) setError(out.error);
      if (out?.pending?.length) {
        setNote(`${plural(out.pending.length, "plugin")} ${out.pending.length === 1 ? "needs" : "need"} you to pick what to share first: open ${out.pending.length === 1 ? "it" : "them"} to finish.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      await readActiveTeam();
      onSwitched();
    }
  };

  return (
    <div className="team-copy">
      <button type="button" className="team-copy__current" aria-haspopup="listbox" aria-expanded={listOpen}
        aria-label={`Your copy: ${shared && team ? team.label : "Local"}`} disabled={busy || team === null}
        onClick={() => setListOpen((v) => !v)}>
        <span className="team-copy__icon" aria-hidden>{shared ? <Icons.Users /> : <Icons.Monitor />}</span>
        <span className="team-copy__text">
          <span className="team-copy__name">{team ? (shared ? team.label : "Local") : "Loading…"}</span>
          <span className="team-copy__sub">{busy ? "Switching…" : team?.phrase ?? ""}</span>
        </span>
        <Dot dot={dot} />
        <span className={`team-copy__chevron${listOpen ? " is-open" : ""}`} aria-hidden><Icons.ChevronDown /></span>
      </button>
      {listOpen ? (
        <div className="team-copy__list" role="listbox" aria-label="Switch the whole app to">
          {(choices ?? [LOCAL]).map((c) => {
            const on = c.id === current;
            return (
              <button key={c.id} type="button" role="option" aria-selected={on} className={`team-copy__option${on ? " is-current" : ""}`}
                disabled={busy} onClick={() => void pick(c)}>
                <span className="team-copy__icon" aria-hidden>{c.kind === "shared" ? <Icons.Users /> : <Icons.Monitor />}</span>
                <span className="team-copy__text">
                  <span className="team-copy__name">{c.kind === "shared" ? c.label : "Local"}</span>
                  <span className="team-copy__sub">
                    {c.kind === "shared" ? (c.members ? plural(c.members, "member") : "Team") : "This PC only"}
                  </span>
                </span>
                <Dot dot={rowDot(c, team)} />
                <span className="team-copy__check" aria-hidden>{on ? <Icons.Check /> : null}</span>
              </button>
            );
          })}
        </div>
      ) : null}
      {error ? <p className="plugin-scope-menu__note plugin-scope-menu__note--warn" role="alert">{error}</p> : null}
      {note ? <p className="plugin-scope-menu__note">{note}</p> : null}
    </div>
  );
}

/**
 * Team and Live in the title bar, one button: green while this PC is live (with how many are
 * in its group), pulsing while it links to someone, the status dot of the copy the app is on (red:
 * conflicts or an error, yellow: something waits or someone live can't be reached, green: in sync)
 * and the count of what needs looking at across the team's plugins (red with a conflict). The same
 * on every tab: it reads the active team, never the plugin pane on screen. The menu starts with the
 * copy picker (Local or a team, for the whole app), then the live groups and people per team, then
 * Settings, which opens the Team modal, as does the activity tray (`OPEN_TEAM_MODAL_EVENT`). Always
 * shown, on the phone too (every call it makes is allowed off the PC).
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
  const dot: CopyDot | "" = team?.dot === "error" ? "error" : warn ? "warn" : team?.dot ?? "";

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
        {dot ? <span className={`plugin-scope-dot plugin-scope-dot--${dot} app-header-team-btn__dot`} data-dot={dot} aria-hidden /> : null}
        {team?.badge ? (
          <span className={`plugin-scope-count app-header-team-btn__count${team.conflicts ? " is-alert" : ""}`}>{team.badge}</span>
        ) : null}
      </button>
      <DropdownPanel anchorRef={anchorRef} open={open} onClose={() => setOpen(false)} width={320} placement="bottom">
        <CopyPicker team={team} onSwitched={() => void readLiveOverview()} />
        {error ? <p className="plugin-scope-menu__note plugin-scope-menu__note--warn live-menu__error" role="alert">{error}</p> : null}
        {teams.length ? (
          <LiveMenu teams={teams} busy={busy} onJoin={join} onLeave={leave} onInvite={invite} follow={follow} onFollow={writeLiveFollow}
            current={team?.current} />
        ) : <div className="plugin-scope-menu__empty">No team yet.</div>}
        <div className="team-live-menu__foot">
          <button type="button" className="team-live-menu__settings" onClick={settings}>
            <span className="team-copy__icon" aria-hidden><Icons.Gear /></span>
            <span>Settings</span>
            {team?.conflicts ? (
              <span className="team-live-menu__alert"><Icons.AlertTriangle /> {plural(team.conflicts, "conflict")}</span>
            ) : null}
          </button>
        </div>
      </DropdownPanel>
      <TeamModal open={modal !== null} initial={modal ?? "overview"} onClose={() => setModal(null)} />
    </div>
  );
}
