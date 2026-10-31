import { useState } from "react";
import type { LiveTeam, LiveTeamGroup, LiveTeamMember, LiveTeamOnline } from "../types/panel";
import { groupName, initials, listText, personTone } from "./scopeBarText";
import "./plugin-ui.css";

/** At most this many faces per group; the count says the rest. */
const FACES = 4;

function Face({ name, id }: { name: string; id: string }) {
  return (
    <span className={`plugin-scope-avatar plugin-scope-avatar--tone-${personTone(id)}`} aria-hidden>
      {initials(name)}
    </span>
  );
}

type Props = {
  teams: LiveTeam[];
  busy: boolean;
  /** Join a teammate's group on a team copy (its room); none: Start live there. */
  onJoin: (team: LiveTeam, room?: string) => void;
  onLeave: (team: LiveTeam) => void;
  /** Ask a teammate who has the app open into this PC's group (starts live first). */
  onInvite: (team: LiveTeam, person: LiveTeamOnline) => void;
  /** Each plugin's page goes to the leader's screen in a group this PC joined. */
  follow: boolean;
  onFollow: (on: boolean) => void;
  /** The team this PC works in (a space id, or "personal"), the same on every tab. */
  current?: string;
  /** Work in this team: every plugin moves to it. */
  onUse?: (team: LiveTeam) => void;
};

const size = (g: LiveTeamGroup) => `${g.members.length} ${g.members.length === 1 ? "person" : "people"}`;

/** Who is in which plugin: "Cy in Kanban, Mo in Cards" (people with nothing on screen are left out). */
function whereText(members: LiveTeamMember[]): string {
  return members.filter((m) => !m.me && m.pluginLabel).map((m) => `${m.name} in ${m.pluginLabel}`).join(", ");
}

function TeamSection({ team, heading, busy, onJoin, onLeave, onInvite, follow, onFollow, current, onUse }: Omit<Props, "teams"> & {
  team: LiveTeam;
  heading: boolean;
}) {
  const mine = team.groups.find((g) => g.mine);
  const others = mine ? mine.members.filter((m) => !m.me) : [];
  const connecting = team.on ? team.connecting ?? [] : [];
  const unreached = team.on ? (team.unlinked ?? []).filter((name) => !connecting.includes(name)) : [];
  // Groupmates on an older app: their screen doesn't follow the group, or they are live in one plugin only.
  const older = team.on ? others.filter((m) => m.older).map((m) => m.name) : [];
  const partial = team.on ? others.filter((m) => m.partial && !m.older) : [];

  return (
    <>
      {onUse && current !== undefined && current !== team.space ? (
        <div className="plugin-scope-menu__foot">
          <button type="button" role="menuitem" className="plugin-scope-btn plugin-scope-btn--block" disabled={busy}
            onClick={() => onUse(team)}>
            Work in this team
          </button>
        </div>
      ) : null}
      {!team.supported ? (
        <div className="plugin-scope-menu__note">{team.reason || "Live isn't available here."}</div>
      ) : (
        <>
          {team.groups.length ? team.groups.map((g) => {
            const where = whereText(g.members);
            return (
              <div key={g.room} className={`plugin-scope-menu__row${g.mine ? " is-mine" : ""}`}>
                <span className="plugin-scope-faces">
                  {g.members.slice(0, FACES).map((m) => <Face key={m.device || m.name} name={m.name} id={m.device || m.user || m.name} />)}
                </span>
                <span className="plugin-scope-menu__text">
                  <span className="plugin-scope-menu__name">{groupName(g)}</span>
                  <span className="plugin-scope-menu__sub">{g.mine ? `${size(g)} · you're in it` : size(g)}</span>
                  {where ? <span className="plugin-scope-menu__sub" title={where}>{where}</span> : null}
                </span>
                {g.mine ? (
                  <button type="button" role="menuitem" className="plugin-scope-btn plugin-scope-btn--row" disabled={busy}
                    onClick={() => onLeave(team)}>
                    Leave
                  </button>
                ) : (
                  <button type="button" role="menuitem" className="plugin-scope-btn plugin-scope-btn--row plugin-scope-btn--primary"
                    disabled={busy} aria-label={`Join ${groupName(g)}`} onClick={() => onJoin(team, g.room)}>
                    Join
                  </button>
                )}
              </div>
            );
          }) : <div className="plugin-scope-menu__empty">Nobody's live in {team.label} yet.</div>}
          {mine && !mine.hostIsMe && team.on ? (
            <label className="plugin-scope-menu__row plugin-scope-menu__check-row">
              <input type="checkbox" checked={follow} onChange={(event) => onFollow(event.target.checked)} />
              <span className="plugin-scope-menu__text">
                <span className="plugin-scope-menu__name">Same screen as {groupName(mine)}</span>
              </span>
            </label>
          ) : null}
          {older.length ? (
            <div className="plugin-scope-menu__note plugin-scope-menu__note--warn">
              {listText(older)} {older.length === 1 ? "needs" : "need"} to update UEFN Ducky. Until then{" "}
              {older.length === 1 ? "their screen doesn't" : "their screens don't"} follow the group, but their tab clicks
              still reach everyone.
            </div>
          ) : null}
          {partial.length ? (
            <div className="plugin-scope-menu__note plugin-scope-menu__note--warn">
              {listText(partial.map((m) => m.name))} {partial.length === 1 ? "needs" : "need"} to update UEFN Ducky to be
              live in every plugin. Until then {partial.length === 1 ? "they're" : "they're each"} live in{" "}
              {listText([...new Set(partial.map((m) => m.pluginLabel || "one plugin"))])} only.
            </div>
          ) : null}
          {connecting.length ? (
            <div className="plugin-scope-menu__note">Connecting to {listText(connecting)}…</div>
          ) : null}
          {unreached.length ? (
            <div className="plugin-scope-menu__note plugin-scope-menu__note--warn">
              Can't reach {listText(unreached)} on this network, so pointers, the shared screen and locks don't reach
              them. Changes still sync.
            </div>
          ) : null}
          {(team.online ?? []).length ? (
            <>
              <div className="plugin-scope-menu__head">Online</div>
              {(team.online ?? []).map((p) => (
                <div key={p.device} className="plugin-scope-menu__row">
                  <span className="plugin-scope-faces"><Face name={p.name} id={p.device || p.user || p.name} /></span>
                  <span className="plugin-scope-menu__text">
                    <span className="plugin-scope-menu__name">{p.name}</span>
                    {p.live ? <span className="plugin-scope-menu__sub">Live in another group</span> : null}
                  </span>
                  <button type="button" role="menuitem" className="plugin-scope-btn plugin-scope-btn--row"
                    disabled={busy || p.invited} aria-label={p.invited ? `Invited ${p.name}` : `Invite ${p.name}`}
                    onClick={() => onInvite(team, p)}>
                    {p.invited ? "Invited" : "Invite"}
                  </button>
                </div>
              ))}
            </>
          ) : null}
          {/* Start live goes into a teammate's group when one is live (the biggest), else hosts
              this PC's own: two people who both press it end up together. */}
          {!team.on ? (
            <div className="plugin-scope-menu__foot">
              <button type="button" role="menuitem" className="plugin-scope-btn plugin-scope-btn--block" disabled={busy}
                aria-label={heading ? `Start live in ${team.label}` : "Start live"} onClick={() => onJoin(team)}>
                Start live
              </button>
            </div>
          ) : null}
        </>
      )}
    </>
  );
}

/** Everyone on a team copy who has the app open: live (in any group) or online. */
function headCount(team: LiveTeam): number {
  const ids = new Set<string>();
  for (const g of team.groups) for (const m of g.members) ids.add(m.device || m.user || m.name);
  for (const p of team.online ?? []) ids.add(p.device || p.user || p.name);
  return ids.size;
}

/**
 * The title bar's Team and Live menu. Tabs: Live (team copies where someone is live) and All.
 * Each team copy is an accordion (your team, or the one this PC is live in, starts open): its
 * live groups (named by host, who is in which plugin) with Join / Leave, Same screen as the
 * group, who else is online with Invite, Start live, and Work in this team. Being live
 * covers every plugin on that team copy: people live together see each other's pointers, and
 * what one presses is greyed out for the others.
 */
export function LiveMenu({ teams, ...rest }: Props) {
  const anyLive = teams.some((t) => t.groups.length);
  const [tab, setTab] = useState<"live" | "all">(anyLive ? "live" : "all");
  const [openTeams, setOpenTeams] = useState<Set<string>>(() => {
    const first = teams.find((t) => t.space === rest.current) ?? teams.find((t) => t.on) ?? (teams.length === 1 ? teams[0] : undefined);
    return new Set(first ? [first.space] : []);
  });
  const shown = tab === "live" ? teams.filter((t) => t.groups.length) : teams;
  const several = teams.length > 1;
  const toggle = (space: string) => setOpenTeams((prev) => {
    const next = new Set(prev);
    if (!next.delete(space)) next.add(space);
    return next;
  });
  return (
    <div className="plugin-scope-menu live-menu" role="menu" aria-label="Live">
      <div className="live-menu__tabs" role="tablist" aria-label="Show">
        {([["live", "Live"], ["all", "All"]] as const).map(([id, text]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id}
            className={`live-menu__tab${tab === id ? " is-active" : ""}`} onClick={() => setTab(id)}>
            {text}
          </button>
        ))}
      </div>
      {shown.length ? shown.map((team) => {
        const isOpen = openTeams.has(team.space);
        const people = headCount(team);
        return (
          <div key={team.space} className="live-menu__team">
            <button type="button" role="menuitem" className="plugin-scope-menu__row live-menu__team-head" aria-expanded={isOpen}
              onClick={() => toggle(team.space)}>
              <span className={`live-menu__caret${isOpen ? " is-open" : ""}`} aria-hidden>▸</span>
              <span className="plugin-scope-menu__text">
                <span className="plugin-scope-menu__name">{team.label}</span>
                <span className="plugin-scope-menu__sub">
                  {people ? `${people} ${people === 1 ? "person" : "people"}` : "Nobody online"}
                  {team.space === rest.current ? " · your team" : ""}
                </span>
              </span>
              {team.groups.length ? <span className="plugin-scope-dot plugin-scope-dot--live" title="Someone is live" aria-hidden /> : null}
            </button>
            {isOpen ? <div className="live-menu__team-body"><TeamSection team={team} heading={several} {...rest} /></div> : null}
          </div>
        );
      }) : <div className="plugin-scope-menu__empty">Nobody's live right now.</div>}
    </div>
  );
}
