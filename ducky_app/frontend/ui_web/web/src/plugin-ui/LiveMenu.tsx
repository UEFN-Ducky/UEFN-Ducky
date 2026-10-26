import type { LiveTeam, LiveTeamGroup, LiveTeamMember } from "../types/panel";
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
  /** Each plugin's page goes to the leader's screen in a group this PC joined. */
  follow: boolean;
  onFollow: (on: boolean) => void;
};

const size = (g: LiveTeamGroup) => `${g.members.length} ${g.members.length === 1 ? "person" : "people"}`;

/** Who is in which plugin: "Cy in Kanban, Mo in Cards" (people with nothing on screen are left out). */
function whereText(members: LiveTeamMember[]): string {
  return members.filter((m) => !m.me && m.pluginLabel).map((m) => `${m.name} in ${m.pluginLabel}`).join(", ");
}

function TeamSection({ team, heading, busy, onJoin, onLeave, follow, onFollow }: Omit<Props, "teams"> & {
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
      {heading ? <div className="plugin-scope-menu__head">{team.label}</div> : null}
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
                <span className="plugin-scope-menu__name">Same screen as the group</span>
                <span className="plugin-scope-menu__sub">
                  Tabs, play and scrolling go both ways, through {groupName(mine)}, who leads the group.
                </span>
              </span>
            </label>
          ) : mine?.hostIsMe && mine.members.length > 1 && team.on ? (
            <div className="plugin-scope-menu__note">
              You lead the group: everyone sees your screen, and their tab clicks, play and scrolling come through you.
            </div>
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

/**
 * The title bar's Live menu: the live groups on each team copy (each named by its host, with
 * who is in it and the plugin each has open), Join / Leave, Same screen as the group, and
 * Start live. Being live covers every plugin on that team copy: people live together see each
 * other's pointers, and what one presses is greyed out for the others.
 */
export function LiveMenu({ teams, ...rest }: Props) {
  const several = teams.length > 1;
  return (
    <div className="plugin-scope-menu live-menu" role="menu" aria-label="Live">
      {!several && teams[0] ? <div className="plugin-scope-menu__head">Live in {teams[0].label}</div> : null}
      {teams.map((team, i) => (
        <div key={team.space} className="live-menu__team">
          {i ? <div className="plugin-scope-menu__sep" /> : null}
          <TeamSection team={team} heading={several} {...rest} />
        </div>
      ))}
      <div className="plugin-scope-menu__sep" />
      <div className="plugin-scope-menu__note">
        Live covers every plugin of the team. People live together see each other's pointers and share one screen; what
        anyone presses is locked for the others until they're done. Leave anytime: your changes keep syncing.
      </div>
    </div>
  );
}
