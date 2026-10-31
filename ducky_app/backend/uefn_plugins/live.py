"""Live editing for plugins on a team copy: teammates live in the same group see each
other's pointers, what each one is pressing (held controls grey out for the others) and
short messages, straight from PC to PC. The spaces provider carries them peer to peer
(:meth:`spaces.Provider.live_send`).

Changes go out with sync like any other change, and the moment one is committed this PC
tells its group (``ducky.change``), whose PCs pull it at once. Inside the group each change
to a plugin's docs and files also travels at once over the link and lands as a pulled copy
would (``ducky.data``, see "live data" below; a manifest's ``"data_live": false`` opts out),
the newest change of an item wins and members never lock each other out; sync still records
every change, and teammates outside the group get it that way.

Every plugin gets this with no plugin code: the host script in every panel page
(``ui_kit.LIVE_PRESENCE_SCRIPT``) sends the pointer, selections and holds, and draws the
group's.

Holds (a control someone is pressing) reach everyone, live or not: the group at once, and
every teammate through presence (:func:`hold` puts them in this PC's presence, and
:func:`roster_holds` hands the panels what teammates' presence says). Each hold names the
view it was pressed in (``v``), so it only greys out the same control on the same screen.
A follower (Same screen as the group, in the Live menu) gets the host's view, scroll and
tab clicks (``ducky.view``, ``ducky.nav``).

A group is a room named after its host's PC (``h:<device id>``): Start live goes into the
biggest group a teammate is live in, or hosts this PC's own when nobody is; Join goes into
a chosen one (:func:`team_groups`). Being in a group is one membership per team copy, not
per plugin (:func:`join_team`): every plugin of the copy here is in it, a plugin switched
to the copy, enabled or opened later joins on its own, closing a tab ends nothing, and
Leave takes them all out (:func:`leave_team`). Each plugin's group is still worked out on
its own (:func:`groups`, its leader, its data); they are all in the same room. Presence
carries each PC's room, at its top and per plugin, so everyone works out the same groups.
Two PCs that started live at about the same time each host a group of one until a roster
shows them the other's: then the one with the higher device id joins the other's
(:func:`merge_lone_groups`).

Plugins may add their own short-lived messages (``live.send`` / ``api.live.send``) and
join named rooms (``api.live.join(room)``). Topics under ``ducky.`` are the host's.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)

LIMIT_BYTES = 16 * 1024  # one message, as JSON
LIMIT_PER_S = 30  # messages a second per plugin
FAST_PER_S = 25  # pointers and selections a second per plugin
CHANGE = "ducky.change"  # "I committed: pull now"
HOLD = "ducky.hold"  # a control this PC is pressing (on) or let go of
NAV = "ducky.nav"  # a tab clicked: the group's screen clicks it too
FAST_TOPICS = frozenset({"ducky.pointer", "ducky.select", "ducky.view"})  # may drop: the next one replaces it
HOST_TOPICS = FAST_TOPICS | {HOLD, NAV}
# Each PC's own live state in a plugin (where its camera is, the tool in hand): a plugin
# sets it, and everyone in the group sees everyone's (:func:`set_my_state`).
STATE = "ducky.state"
STATE_MAX_BYTES = 4 * 1024
STATE_EVERY_S = 5.0  # sent again this often, so a PC that just joined gets it
STATE_STALE_S = 15.0  # a groupmate's state not heard of this long is gone (they left, or crashed)
# The group's shared values of a plugin's components, through its leader (:func:`share`).
SHARE = "ducky.share"
SHARE_MAX_BYTES = 8 * 1024
SHARE_KEYS_MAX = 64
_SHARE_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
HOLD_STALE_S = 30.0  # the page renews a hold every 10 s; one not renewed this long is gone
HOLDS_MAX = 4  # holds per plugin a PC puts in its presence (the sealed blob is 8 KiB at most)
LINK_GRACE_S = 10.0  # a group member with no link this long after both were live is unreachable
JOIN_RELOAD_S = 60.0  # the first pull this soon after joining a teammate's group reloads the panels
# Live data (manifest "data_live"): each change of the plugin's docs and files goes to the
# group at once over its link, and lands there as a pulled copy would (not this PC's to send).
DATA = "ducky.data"
DATA_INLINE_MAX = 36 * 1024  # bytes of a doc or file carried in the message; bigger ones are pulled
OUTBOX_EVERY_S = 0.25  # this PC's new writes are looked for this often while live
HOLD_MAX_S = 90.0  # a change the group's leader hasn't sent for this PC by then goes out from here
ECHO_KEEP = 8  # versions of an item this PC sent its group that it still knows as its own
ROOM_KEY = "live_room:"  # workspace_state: where each plugin is live here (read by every process)
TEAM_CHOICE_KEY = "live_team:"  # workspace_state: the group the person went live in on a team copy
START = "start"  # the choice Start live: a teammate's group, else this PC's own
LONE_GRACE_S = 60.0  # a groupmate gone from the roster this long (a crash, a restart) counts as left
INVITE_S = 30.0  # an invite to this PC's group stands this long, then the popup goes
INVITES_MAX = 8  # invites a PC puts in its presence at once
_ANCHOR_RE = re.compile(r"^[^\x00-\x1f]{1,400}$")
_VIEW_RE = re.compile(r"^[A-Za-z0-9._:-]{0,64}$")
_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
_ROOM_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
HOST_PREFIX = "h:"
# What this app's live speaks, said in presence with the room: from 2 (RELAY_V) the group runs
# through its leader (asks, shared values, data); from 3 (DATA_V) every plugin's data goes to
# the group, not only a plugin's that asks for it; from 4 (TEAM_V) one Live covers every
# plugin of the team copy. An app from before 2 (1.2.433 and older) relays nothing: it never
# leads a group someone up to date is in, its changes are its own to send, and its screen
# doesn't follow the group (the Live menu says who needs to update).
PROTOCOL = 4
RELAY_V = 2
DATA_V = 3
TEAM_V = 4
RELAY_SINCE = (1, 2, 434)  # the first app version whose groups run through a leader (no "v" yet)
LEGACY_MAX = 500


class _State:
    __slots__ = ("account", "room", "since", "space")

    def __init__(self, account: str, space: str, room: str, since: float) -> None:
        self.account, self.space, self.room, self.since = account, space, room, since


class _Team:
    __slots__ = ("room", "since")

    def __init__(self, room: str, since: float) -> None:
        self.room, self.since = room, since


_lock = threading.Lock()
_team_lock = threading.RLock()  # one team copy's plugins are moved by one thread at a time
_live: dict[str, _State] = {}  # plugin -> where it is live on this PC
_teams: dict[tuple[str, str], _Team] = {}  # (account, space) -> the group this PC is live in on that team copy
_own: set[str] = set()  # plugins in a room of their own naming (api.live.join(room)), outside their team's group
_holds: dict[str, dict[str, tuple[float, str, float]]] = {}  # plugin -> anchor -> (pressed at, view, renewed)
_told_holds: dict[tuple[str, str], list[dict[str, Any]]] = {}  # (space, plugin) -> teammates' holds the panels have
_sent: dict[tuple[str, bool], collections.deque[float]] = {}
_listeners: dict[str, dict[str, list[Callable[..., Any]]]] = {}
_kept: set[str] = set()  # folder plugins restarted while live with their panels kept loaded
_join_reload: dict[str, float] = {}  # plugin -> until when its join's pull reloads its panels
_moving: set[tuple[str, str]] = set()  # team copies whose plugins a background join is moving
_company: dict[tuple[str, str], tuple[float, frozenset[str]]] = {}  # team copy -> (last roster with others, them)
_ItemKey = tuple[str, str, str, str, str]  # (account, space, plugin, kind, key)
_sent_shas: dict[_ItemKey, collections.deque[str]] = {}  # versions of an item this PC sent its group
_relayed: dict[_ItemKey, dict[str, str]] = {}  # a groupmate's change this PC took on as leader: sha, who made it
_led_by: dict[str, str] = {}  # plugin -> the device that led this PC's group at the last look
_my_states: dict[str, dict[str, Any]] = {}  # plugin -> {state, sent}: this PC's live state
_their_states: dict[str, dict[str, dict[str, Any]]] = {}  # plugin -> device -> {from, state, at}
_shared: dict[str, dict[str, dict[str, Any]]] = {}  # plugin -> key -> {v, ver, by}: the group's values
_shared_sent: dict[str, float] = {}  # plugin -> when the leader last sent them all again
_marks: dict[tuple[str, str, str], float] = {}  # (account, space, plugin) -> newest write sent live
_outbox: list[threading.Thread] = []
_legacy: set[str] = set()  # devices whose messages only an app before RELAY_SINCE sends
_noted: dict[tuple[str, str, str], float] = {}  # (account, space, plugin) -> when the last commit note went
NOTE_GAP_S = 1.0  # a commit note at most this often while a round still sends
_beating: dict[tuple[str, str], bool] = {}  # (account, space) -> a beat is going; True: one more after it
_invites: dict[tuple[str, str], dict[str, tuple[str, float]]] = {}  # team copy -> device -> (room, sent at)
_told_invites: set[tuple[str, float]] = set()  # (inviter's device, sent at) this PC already showed


def _push(event: dict[str, Any]) -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event(event)
    except Exception:  # noqa: BLE001 — the next status read shows it
        _log.debug("live push failed", exc_info=True)


def _in_bridge() -> bool:
    from backend.process_role import role

    return role() != "app"  # a tool server or an agent host: live runs in the window


def _device() -> str:
    from backend.store.repos import space_vcs

    return space_vcs.device_id()


def host_room(device: str = "") -> str:
    """The room of the group this PC hosts (another PC's with ``device``)."""
    return HOST_PREFIX + (device or _device())


def team_of(plugin: str) -> tuple[str, str] | None:
    return _team(plugin)


def _team(plugin: str) -> tuple[str, str] | None:
    """``(account, space)`` of ``plugin``'s team copy, or None when it shows Local."""
    from backend.uefn_plugins import scopes

    scope = scopes.active_scope(plugin)
    if scope.get("kind") != "shared" or scope.get("readOnly"):
        return None
    return str(scope["account"]), str(scope["id"])


def supported(plugin: str) -> tuple[bool, str]:
    """Whether ``plugin`` can go live here, and why not."""
    from backend.uefn_plugins import spaces

    if _in_bridge():
        return False, "Live runs in the UEFN Ducky window."
    team = _team(plugin)
    if team is None:
        return False, "Live works on a team copy."
    try:
        from backend.store.repos import space_vcs

        v2 = space_vcs.head_get(team[0], team[1], plugin)["engine"] == "v2"
    except Exception:  # noqa: BLE001 — an app without heads: no presence to meet in
        v2 = False
    if not v2:
        return False, "Live works once this copy is on the team's new sync."
    impl = spaces.provider()
    try:
        ok = bool(impl.live_supported())
    except Exception:  # noqa: BLE001 — a provider that can't say has no live
        ok = False
    return (True, "") if ok else (False, "This team service has no live editing.")


def _current(plugin: str) -> _State | None:
    """Where ``plugin`` is live, dropped when its data no longer shows that team."""
    with _lock:
        st = _live.get(plugin)
    if st is None:
        return None
    team = _team(plugin)
    if team != (st.account, st.space):
        leave(plugin)
        return None
    return st


def scope_switched(plugin: str) -> None:
    """``plugin`` now shows another copy: it leaves its old team copy's group at once (never
    on the next roster, so teammates stop seeing it now), this PC stops being live on that
    copy when no plugin is left there, and on a copy this PC is live on it joins the group."""
    with _lock:
        st = _live.get(plugin)
        own = plugin in _own
    _current(plugin)
    if st is not None and not own and _team(plugin) != (st.account, st.space):
        if not _team_plugins(st.account, st.space)[0]:
            leave_team(st.account, st.space)
    _come_along(plugin)


def is_live(plugin: str) -> bool:
    with _lock:
        return plugin in _live


def spaces_live() -> set[tuple[str, str]]:
    """``(account, space)`` of every team copy this PC is live on (the scheduler runs their
    rounds every few seconds)."""
    with _lock:
        return {(st.account, st.space) for st in _live.values()} | set(_teams)


def team_room(account: str, space: str) -> dict[str, Any] | None:
    """What presence says at its top while this PC is live on that team copy."""
    with _lock:
        team = _teams.get((account, space))
    return None if team is None else {"room": team.room, "since": team.since, "v": PROTOCOL}


def room_of(plugin: str, account: str, space: str) -> dict[str, Any] | None:
    """What presence says of ``plugin`` while it is live in that space."""
    with _lock:
        st = _live.get(plugin)
    if st is None or (st.account, st.space) != (account, space):
        return None
    return {"room": st.room, "since": st.since, "v": PROTOCOL}


def _beat_soon(account: str, space: str) -> None:
    """Presence carries the room: tell the team now instead of at the next minute's beat. One
    beat a space at a time, and one more after it when the room moved while it went: two at
    once (Join leaves one room and goes into another) could land the older last, and the team
    would see this PC where it no longer is until the scheduler's next beat."""
    key = (account, space)
    with _lock:
        if key in _beating:
            _beating[key] = True
            return
        _beating[key] = False

    def work() -> None:
        while True:
            try:
                from backend.store.repos import plugin_data as repo
                from backend.uefn_plugins import spaces, vcs_engine

                plugins = sorted(p for p, s in repo.links(account).items() if s == space)
                team = spaces.ledgers(account, space, plugins)
                if team:
                    vcs_engine.beat(account, space, next(iter(team.values())), list(team), force=True)
            except Exception:  # noqa: BLE001 — the scheduler's next beat says it anyway
                _log.debug("live beat failed", exc_info=True)
            with _lock:
                if not _beating.get(key):
                    _beating.pop(key, None)
                    return
                _beating[key] = False

    threading.Thread(target=work, name="live-beat", daemon=True).start()


def _teammates_group(account: str, space: str) -> str:
    """The room of the biggest group a teammate is live in on the team copy ('' for none)."""
    mine = host_room()
    try:
        found = [g for g in team_groups(account, space) if g["room"] != mine]
    except Exception:  # noqa: BLE001 — no roster yet: this PC hosts its own
        _log.debug("live groups of %s failed", space, exc_info=True)
        return ""
    found.sort(key=lambda g: -len(g["members"]))
    return str(found[0]["room"]) if found else ""


def _enabled(plugin: str) -> bool:
    from backend.uefn_plugins.host import is_plugin_enabled

    return is_plugin_enabled(plugin)


def _team_plugins(account: str, space: str) -> tuple[list[str], str]:
    """The plugins here that go live with the team copy (enabled, showing its data, able to go
    live), and why none can."""
    from backend.store.repos import plugin_data as repo

    if _in_bridge():
        return [], "Live runs in the UEFN Ducky window."
    out: list[str] = []
    why = "No plugin here shows this team's data."
    for plugin in sorted(p for p, s in repo.links(account).items() if s == space):
        with _lock:
            own = plugin in _own
        if own or not _enabled(plugin):
            continue
        ok, reason = supported(plugin)
        if ok and _team(plugin) == (account, space):
            out.append(plugin)
        elif not ok:
            why = reason
    return out, why


def join(plugin: str, room: str = "", *, reload_panels: bool = True) -> dict[str, Any]:
    """``plugin`` goes live. No room (Start live) or a teammate's group (``h:<their device>``):
    this PC goes live on the plugin's team copy, every plugin of it with it
    (:func:`join_team`). Any other room is the plugin's own (``api.live.join(room)``), for it
    alone. Returns the plugin's :func:`status`."""
    room = str(room or "")
    if room and not _ROOM_RE.match(room):
        return {"ok": False, "error": "A room name is letters, digits, . _ : - (up to 64)."}
    ok, why = supported(plugin)
    if not ok:
        return {"ok": False, "error": why}
    account, space = _team(plugin)  # type: ignore[misc]
    if room and not room.startswith(HOST_PREFIX):
        with _lock:
            _own.add(plugin)
        got = _join_plugin(plugin, account, space, room, time.time(), reload_panels=reload_panels)
        if not got.get("ok"):
            with _lock:
                _own.discard(plugin)
    else:
        with _lock:
            _own.discard(plugin)
        got = join_team(account, space, room, reload_panels=reload_panels)
    return {"ok": True, **status(plugin)} if got.get("ok") else got


def join_team(account: str, space: str, room: str = "", *, reload_panels: bool = True) -> dict[str, Any]:
    """This PC goes live on the team copy: every plugin of it here (:func:`_team_plugins`)
    joins ``room``, a teammate's group (``h:<their device>``), all with one ``since``. No
    room (Start live): the biggest group a teammate is live in, else this PC hosts its own.
    Joining another group leaves the one it was in. A teammate's group is joined up to date:
    each plugin pulls first, and that pull reloads its panels (``reload_panels``) so the page
    opens on the group's data. Plugins that come to the copy later join on their own
    (:func:`catch_up`). Returns :func:`team_status`."""
    room = str(room or "")
    if room and not (room.startswith(HOST_PREFIX) and _ROOM_RE.match(room)):
        return {"ok": False, "error": "A group is named after its host's PC (h:<device id>)."}
    plugins, why = _team_plugins(account, space)
    if not plugins:
        return {"ok": False, "error": why}
    room = room or _teammates_group(account, space) or host_room()
    key = (account, space)
    with _team_lock:
        with _lock:
            old = _teams.get(key)
            if old is None or old.room != room:
                _teams[key] = _Team(room, time.time())
                _company.pop(key, None)
        failed = _reconcile(account, space, reload_panels=reload_panels)
        with _lock:
            joined = any((st.account, st.space, st.room) == (account, space, room) for p, st in _live.items()
                         if p not in _own)
            if not joined:
                _teams.pop(key, None)
    if not joined:
        _team_changed(account, space)
        return {"ok": False, "error": failed or "Couldn't go live."}
    if old is None or old.room != room:
        _team_changed(account, space)  # presence says it with the plugins' beat (_join_plugin)
    return {"ok": True, **team_status(account, space)}


def _reconcile(account: str, space: str, *, reload_panels: bool = True) -> str:
    """Every plugin of the team copy here is in this PC's group on it, and none is while this
    PC isn't live there (a plugin in a room of its own stays in it). Returns why one
    couldn't go live ('' when every one did)."""
    key = (account, space)
    with _team_lock:
        with _lock:
            team = _teams.get(key)
            here = {p: st.room for p, st in _live.items() if (st.account, st.space) == key and p not in _own}
        want = _team_plugins(account, space)[0] if team is not None else []
        failed = ""
        for plugin in want:
            if team is not None and here.get(plugin) != team.room:
                got = _join_plugin(plugin, account, space, team.room, team.since, reload_panels=reload_panels)
                if not got.get("ok"):
                    failed = str(got.get("error") or "")
        for plugin in sorted(set(here) - set(want)):
            leave(plugin)
    return failed


def _join_plugin(plugin: str, account: str, space: str, room: str, since: float, *,
                 reload_panels: bool) -> dict[str, Any]:
    """``plugin`` goes into ``room`` on its team copy, leaving the one it was in."""
    from backend.uefn_plugins import spaces

    with _lock:
        old = _live.get(plugin)
    if old is not None and (old.account, old.space, old.room) == (account, space, room):
        return {"ok": True}
    if old is not None:
        leave(plugin)
    teammates = room != host_room()
    with _lock:
        _live[plugin] = _State(account, space, room, since)
        if teammates and reload_panels:
            _join_reload[plugin] = time.time() + JOIN_RELOAD_S
    try:
        spaces.provider().live_join(account, space, plugin, room)
    except Exception as exc:  # noqa: BLE001 — not live after all; say why
        with _lock:
            _live.pop(plugin, None)
            _join_reload.pop(plugin, None)
        return {"ok": False, "error": f"Couldn't go live: {exc}"}
    _publish_room(plugin, _live.get(plugin))
    _outbox_start()
    _pull_now(account, space, plugin)
    _beat_soon(account, space)
    peers_changed(account, space, plugin)
    return {"ok": True}


def catch_up(account: str, space: str) -> None:
    """A roster came in: while this PC is live on the team copy, a plugin that came to it
    since (switched to the copy, enabled) joins the group and one that went leaves it; while
    it isn't, it goes back into the group the person went live in before a restart, while
    that group is still live (:func:`remember_team`). Joins go in the background (they call
    the team service)."""
    if _in_bridge():
        return
    key = (account, space)
    with _lock:
        live_now = key in _teams
        here = {p for p, st in _live.items() if (st.account, st.space) == key and p not in _own}
    if live_now:
        if set(_team_plugins(account, space)[0]) == here:
            return

        def job() -> None:
            _reconcile(account, space)
    else:
        choice = _remembered_team(account, space, team_groups(account, space))
        if not choice:
            return

        def job() -> None:
            join_team(account, space, "" if choice == START else choice)
    with _lock:
        if key in _moving:
            return  # a join is already on its way (rosters come often)
        _moving.add(key)

    def work() -> None:
        try:
            job()
        except Exception:  # noqa: BLE001 — the next roster tries again
            _log.debug("live catch-up of %s failed", space, exc_info=True)
        finally:
            with _lock:
                _moving.discard(key)

    threading.Thread(target=work, name="live-catch-up", daemon=True).start()


def merge_lone_groups(account: str, space: str) -> list[str]:
    """A roster came in: while this PC hosts a group of one on the team copy and a teammate
    is live in another group of it, this PC joins theirs: the biggest, and of groups of one
    the host with the lowest device id, so of two PCs that started live together only the
    one with the higher id moves. Its panels stay as they are (both were live on the team
    copy). Returns the plugins that moved."""
    me = _device()
    key = (account, space)
    with _lock:
        team = _teams.get(key)
        plugins = sorted(p for p, st in _live.items() if (st.account, st.space) == key and p not in _own)
    if team is None or team.room != host_room(me):
        return []
    try:
        found = team_groups(account, space)
    except Exception:  # noqa: BLE001 — the next roster tries again
        _log.debug("live groups of %s failed", space, exc_info=True)
        return []
    mine = next((g for g in found if g["mine"]), None)
    if mine is None or len(mine["members"]) > 1:
        return []  # someone is in this PC's group: it stays
    others = sorted((g for g in found if not g["mine"] and g["room"].startswith(HOST_PREFIX)),
                    key=lambda g: (-len(g["members"]), g["hostDevice"]))
    if not others:
        return []
    target = others[0]
    if len(target["members"]) == 1 and target["hostDevice"] > me:
        return []  # two groups of one: the other PC (higher id) comes here
    with _lock:
        if key in _moving:
            return []  # the move is already on its way (rosters come often)
        _moving.add(key)

    def move(room: str = str(target["room"])) -> None:
        try:
            join_team(account, space, room, reload_panels=False)
        finally:
            with _lock:
                _moving.discard(key)

    threading.Thread(target=move, name="live-merge", daemon=True).start()
    return plugins


def end_lone_groups(account: str, space: str, *, now: float | None = None) -> list[str]:
    """A roster came in: Live ends here on the team copy when this PC's group had someone
    else in it and now has only this PC, at once when they left it (they're still on the
    roster) and after :data:`LONE_GRACE_S` when they vanished from it (a crash, a restart:
    they may be back). The panels stay where they are and sync as before. A group this PC
    just started, that nobody joined yet, stays. Returns the plugins whose Live ended."""
    from backend.store.repos import space_vcs

    now = time.time() if now is None else now
    key = (account, space)
    with _lock:
        team = _teams.get(key)
        plugins = sorted(p for p, st in _live.items() if (st.account, st.space) == key and p not in _own)
    if team is None:
        return []
    roster = space_vcs.presence_get(account, space)["roster"]
    people = roster.get("people") if isinstance(roster, dict) else None
    listed = {str(p.get("device") or "") for p in people if isinstance(p, dict)} if isinstance(people, list) else set()
    try:
        mine = next((g for g in team_groups(account, space) if g["mine"]), None)
    except Exception:  # noqa: BLE001 — the next roster looks again
        _log.debug("live groups of %s failed", space, exc_info=True)
        return []
    others = frozenset(m["device"] for m in (mine or {}).get("members") or [] if not m["me"])
    with _lock:
        if others:
            _company[key] = (now, others)
            return []
        seen = _company.get(key)
    if seen is None:
        return []
    at, devices = seen
    if not devices & listed and now - at < LONE_GRACE_S:
        return []  # gone from the roster: maybe back in a moment
    leave_team(account, space, why="alone")
    return plugins


def remember_team(account: str, space: str, choice: str) -> None:
    """The group the person went live in on the team copy (``start``, or a teammate's room),
    so this PC goes back into it after a restart (:func:`catch_up`); '' forgets it (Leave,
    alone)."""
    from backend.store.repos import kv

    try:
        if choice:
            kv.set_doc("workspace_state", TEAM_CHOICE_KEY + space, {"account": account, "choice": str(choice)})
        else:
            kv.delete_doc("workspace_state", TEAM_CHOICE_KEY + space)
    except Exception:  # noqa: BLE001 — the person goes live again by hand
        _log.debug("remembering the live choice of %s failed", space, exc_info=True)


def _remembered_team(account: str, space: str, groups_: list[dict[str, Any]]) -> str:
    """What this PC goes back into: ``start``, or a teammate's room while it is still live."""
    from backend.store.repos import kv

    try:
        doc = kv.get_doc("workspace_state", TEAM_CHOICE_KEY + space)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(doc, dict) or doc.get("account") != account:
        return ""
    choice = str(doc.get("choice") or "")
    if choice == START or any(g["room"] == choice for g in groups_):
        return choice
    return ""


def panels_kept(plugin: str) -> None:
    """A folder plugin restarted for a teammate's pull while live, its panels kept loaded
    (``host._restart_moved``): they reload once when Live ends."""
    with _lock:
        _kept.add(plugin)


def keeps_panels(plugin: str) -> bool:
    """Whether a live pull restarts ``plugin`` with its panels kept loaded: only while this PC
    presses or types in one of them (a reload would take that edit away); they reload as soon
    as it lets go (:func:`hold`). Otherwise the panels reload and show the teammate's change at
    once, as they do when Live ends. The first pull after joining a teammate's group always
    reloads them, so the page opens on the group's data."""
    with _lock:
        if plugin not in _live:
            return False
        until = _join_reload.pop(plugin, 0.0)
        held = bool(_holds.get(plugin))
    return held and time.time() >= until


def _reload_kept(plugin: str) -> None:
    with _lock:
        if plugin not in _kept:
            return
        _kept.discard(plugin)
    try:
        from backend.store.repos import folder_files

        folder_files.reload_bump(plugin, "live ended")  # the app restarts it with its panels
    except Exception:  # noqa: BLE001 — its next pull or switch reloads them
        _log.debug("reload after live of %s failed", plugin, exc_info=True)


def leave_live(plugin: str) -> dict[str, Any]:
    """Leave, from a plugin (its bar, its page, its backend): out of its own room when it is
    in one (back into its team's group, while this PC is live there), else this PC leaves its
    team copy's group, every plugin with it."""
    with _lock:
        own = plugin in _own
        _own.discard(plugin)
        st = _live.get(plugin)
    if own:
        leave(plugin)
        if st is not None:
            _reconcile(st.account, st.space)
        return {"ok": True}
    team = (st.account, st.space) if st is not None else _team(plugin)
    return leave_team(*team) if team is not None else leave(plugin)


def leave_team(account: str, space: str, *, why: str = "", forget: bool = True) -> dict[str, Any]:
    """This PC stops being live on the team copy: every plugin of it leaves the group (a
    plugin in a room of its own stays in it). ``why``: ``alone`` when Live ended by itself
    (the menu says so). ``forget``: no going back in after a restart."""
    key = (account, space)
    with _team_lock:
        with _lock:
            had = _teams.pop(key, None)
            _company.pop(key, None)
            plugins = sorted(p for p, st in _live.items() if (st.account, st.space) == key and p not in _own)
        if forget:
            remember_team(account, space, "")
        for plugin in plugins:
            leave(plugin, why=why)
    if had is not None or plugins:
        _beat_soon(account, space)
        _team_changed(account, space, ended=why)
    return {"ok": True}


def _team_changed(account: str, space: str, *, ended: str = "") -> None:
    """The title bar's Live menu reads the team copy again (``ended``: why Live ended by itself)."""
    with _lock:
        on = (account, space) in _teams
    _push({"type": "live_team", "account": account, "space": space, "on": on, **({"ended": ended} if ended else {})})


def leave(plugin: str, *, why: str = "") -> dict[str, Any]:
    """``plugin`` stops being live here (its team copy's group goes on: :func:`leave_team`
    ends that). ``why``: ``alone`` when Live ended by itself (the bar says so)."""
    from backend.uefn_plugins import spaces

    with _lock:
        st = _live.pop(plugin, None)
        _join_reload.pop(plugin, None)
        _led_by.pop(plugin, None)
        _my_states.pop(plugin, None)
        _their_states.pop(plugin, None)
        _shared.pop(plugin, None)
        _shared_sent.pop(plugin, None)
    _reload_kept(plugin)
    if st is not None:
        _publish_room(plugin, None)
        with _lock:
            _marks.pop((st.account, st.space, plugin), None)
        try:
            spaces.provider().live_leave(st.account, st.space, plugin)
        except Exception:  # noqa: BLE001 — the room lease runs out by itself
            _log.debug("live leave of %s failed", plugin, exc_info=True)
        _beat_soon(st.account, st.space)
        peers_changed(st.account, st.space, plugin, ended=why)
    return {"ok": True}


def leave_all() -> None:
    """The team service went (sign-out, its plugin unloaded): nothing is live, and each team
    copy's choice is kept for when it is back (:func:`catch_up`)."""
    with _lock:
        teams = list(_teams)
    for key in teams:
        leave_team(*key, forget=False)
    with _lock:
        plugins = list(_live)
        _own.clear()
    for plugin in plugins:
        leave(plugin)


def _rate_ok(plugin: str, fast: bool) -> bool:
    now = time.monotonic()
    cap = FAST_PER_S if fast else LIMIT_PER_S
    with _lock:
        q = _sent.setdefault((plugin, fast), collections.deque())
        while q and now - q[0] >= 1.0:
            q.popleft()
        if len(q) >= cap:
            return False
        q.append(now)
    return True


def send(plugin: str, topic: str, data: Any, *, to: str = "", fast: bool = False) -> dict[str, Any]:
    """A short message to the room (or to one Ducky, ``to`` = its device id)."""
    from backend.uefn_plugins import spaces

    topic = str(topic or "")
    if topic not in HOST_TOPICS and (topic.startswith("ducky.") or not _TOPIC_RE.match(topic)):
        return {"ok": False, "error": "A topic is a-z, 0-9, . _ - (up to 40); ducky.* is the host's."}
    try:
        text = json.dumps(data, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Live data must be JSON."}
    if len(text) > LIMIT_BYTES:
        return {"ok": False, "error": f"A live message is at most {LIMIT_BYTES // 1024} KB."}
    st = _current(plugin)
    if st is None:
        return {"ok": False, "error": "This plugin isn't live."}
    # A member's own move asking the leader (``ask``) must arrive, even of a fast topic.
    fast = bool(fast or (topic in FAST_TOPICS and not (isinstance(data, dict) and data.get("ask"))))
    if not _rate_ok(plugin, fast):
        return {"ok": False, "error": "Too many live messages: at most 30 a second."}
    try:
        sent = spaces.provider().live_send(st.account, st.space, plugin, topic, json.loads(text),
                                           to=str(to or ""), fast=fast)
    except Exception as exc:  # noqa: BLE001 — say why instead of failing the caller
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "sent": int(sent or 0)}


# --------------------------------------------------------------------------- live state
#
# Each PC's own state in a plugin, for things only the moment needs: where its camera is,
# the tool in its hand, what it plays. A plugin sets this PC's (latest wins, a JSON object
# up to 4 KB) and every groupmate's panels and backend see everyone's, each by who it is;
# one that joins later gets them within :data:`STATE_EVERY_S`, and a groupmate who left
# drops out after :data:`STATE_STALE_S`. Never data: nothing of it is kept or synced.


def set_my_state(plugin: str, state: Any) -> dict[str, Any]:
    """This PC's live state in ``plugin`` (a JSON object, or None to clear it), to everyone in
    its group at once. Up to 25 a second go out; one over that goes with the next."""
    if state is not None and not isinstance(state, dict):
        return {"ok": False, "error": "A live state is a JSON object (or null to clear it)."}
    try:
        text = json.dumps(state, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "A live state must be JSON."}
    if len(text) > STATE_MAX_BYTES:
        return {"ok": False, "error": f"A live state is at most {STATE_MAX_BYTES // 1024} KB."}
    st = _current(plugin)
    if st is None:
        return {"ok": False, "error": "This plugin isn't live."}
    with _lock:
        if state is None:
            _my_states.pop(plugin, None)
        else:
            _my_states[plugin] = {"state": json.loads(text), "sent": 0.0}
    if state is not None and not _rate_ok(plugin, True):
        return {"ok": True, "sent": 0}  # the next turn of the outbox carries it
    return {"ok": True, "sent": _send_state(plugin, st, None if state is None else json.loads(text))}


def _send_state(plugin: str, st: _State, state: Any) -> int:
    from backend.uefn_plugins import spaces

    try:
        sent = spaces.provider().live_send(st.account, st.space, plugin, STATE, {"s": state}, to="",
                                           fast=state is not None)  # a clear must arrive
    except Exception:  # noqa: BLE001 — the next turn sends it again
        _log.debug("sending the live state of %s failed", plugin, exc_info=True)
        return 0
    with _lock:
        if plugin in _my_states:
            _my_states[plugin]["sent"] = time.time()
    return int(sent or 0)


def member_states(plugin: str) -> list[dict[str, Any]]:
    """Every groupmate's live state in ``plugin``: ``from`` (name, user, device, label) and
    ``state`` (a page that just loaded)."""
    with _lock:
        return [{"from": dict(s["from"]), "state": s["state"]} for s in (_their_states.get(plugin) or {}).values()]


def _their_state(plugin: str, who: dict[str, str], data: Any) -> None:
    state = data.get("s") if isinstance(data, dict) else None
    device = who.get("device", "")
    if not device or (state is not None and not isinstance(state, dict)):
        return
    with _lock:
        mine = _their_states.setdefault(plugin, {})
        if state is None:
            if mine.pop(device, None) is None:
                return
        else:
            same = (mine.get(device) or {}).get("state") == state
            mine[device] = {"from": dict(who), "state": state, "at": time.time()}
            if same:
                return  # sent again for latecomers: nothing new to show
    _push({"type": "live_member_state", "plugin": plugin, "from": who, "state": state})
    with _lock:
        callbacks = list(_listeners.get(plugin, {}).get(STATE, ()))
    for cb in callbacks:
        try:
            cb(who, state)
        except Exception:  # noqa: BLE001 — a plugin's callback never breaks delivery
            _log.warning("%s: live state callback failed", plugin, exc_info=True)


def _state_tick(now: float | None = None) -> None:
    """This PC's states again for latecomers; groupmates' not heard of lately go."""
    now = time.time() if now is None else now
    with _lock:
        due = [(p, dict(s)) for p, s in _my_states.items() if now - float(s["sent"]) >= STATE_EVERY_S]
        gone = [(p, d, dict(s["from"])) for p, theirs in _their_states.items() for d, s in theirs.items()
                if now - float(s["at"]) >= STATE_STALE_S]
        for plugin, device, _who in gone:
            _their_states.get(plugin, {}).pop(device, None)
    for plugin, s in due:
        st = _current(plugin)
        if st is not None:
            _send_state(plugin, st, s["state"])
    for plugin, _device, who in gone:
        _push({"type": "live_member_state", "plugin": plugin, "from": who, "state": None})
    _share_tick(now)


# --------------------------------------------------------------------------- shared state
#
# The group's own state of a plugin's components (a player playing from 12 s, the clip
# picked, a panel open): one value per key for the whole group, through its leader like the
# screen. A plugin shares a key's value; on a member it shows there at once and goes to the
# leader (``ask``), which takes the values in the order it hears them and tells everyone
# with a version, so every page ends on the same one. The leader sends each key again every
# :data:`STATE_EVERY_S`, so a PC that just joined has them all. Never data: nothing of it is
# kept or synced (what matters after the group goes belongs in ``api.data``).


def _my_who() -> dict[str, Any]:
    return {"name": "You", "device": _device(), "me": True}


def _wire_by(by: dict[str, Any]) -> dict[str, Any]:
    """Who changed a value, as it goes to the group: this PC by its device only (each PC
    names it from who sent the message)."""
    return {"device": str(by.get("device") or "")} if by.get("me") else {k: str(by.get(k) or "") for k in ("name", "device")}


def _lead_of(plugin: str, st: _State) -> str:
    """The device leading this PC's group of ``plugin`` ('' when this PC leads, or is alone)."""
    mine = _my_group(plugin, st.account, st.space)
    if not mine or len(mine["members"]) < 2 or mine["hostIsMe"]:
        return ""
    return str(mine["hostDevice"])


def share(plugin: str, key: str, value: Any) -> dict[str, Any]:
    """The group's value of ``key`` in ``plugin`` (JSON up to 8 KB; None drops the key),
    through its leader. Up to :data:`SHARE_KEYS_MAX` keys a plugin."""
    from backend.uefn_plugins import spaces

    key = str(key or "")
    if not _SHARE_KEY_RE.match(key):
        return {"ok": False, "error": "A shared key is letters, digits, . _ : - (up to 64)."}
    try:
        text = json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "A shared value must be JSON."}
    if len(text) > SHARE_MAX_BYTES:
        return {"ok": False, "error": f"A shared value is at most {SHARE_MAX_BYTES // 1024} KB."}
    st = _current(plugin)
    if st is None:
        return {"ok": False, "error": "This plugin isn't live."}
    with _lock:
        if value is not None and key not in _shared.get(plugin, {}) and len(_shared.get(plugin, {})) >= SHARE_KEYS_MAX:
            return {"ok": False, "error": f"A plugin shares {SHARE_KEYS_MAX} keys at most."}
    value = json.loads(text)
    lead = _lead_of(plugin, st)
    try:
        if lead:  # shows here at once; the leader's answer (in its order) settles it
            _take_share(plugin, key, value, _my_who(), ver=None)
            spaces.provider().live_send(st.account, st.space, plugin, SHARE, {"k": key, "v": value, "ask": 1},
                                        to=lead, fast=False)
        else:
            ver = _take_share(plugin, key, value, _my_who())
            spaces.provider().live_send(st.account, st.space, plugin, SHARE, {"k": key, "v": value, "ver": ver},
                                        to="", fast=False)
    except Exception as exc:  # noqa: BLE001 — say why instead of failing the caller
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True}


def _take_share(plugin: str, key: str, value: Any, by: dict[str, Any], *, ver: int | None = -1) -> int:
    """``key`` takes ``value``: ``ver`` -1 is the next version (the leader's own order), None
    leaves the version (a member's own, until the leader's answer), else the leader's. While
    a member's own waits, the leader's older value (a resend) never puts the old one back.
    The panels and backend hear it when the value moved. Returns the version."""
    with _lock:
        mine = _shared.setdefault(plugin, {})
        cur = mine.get(key)
        have = int(cur["ver"]) if cur else 0
        if ver is not None and ver >= 0 and (ver < have or (cur is not None and cur.get("pending") and ver <= have)):
            return have  # older than what is here, or than this PC's own waiting for the leader
        new_ver = have + 1 if ver == -1 else have if ver is None else int(ver)
        moved = cur is None or cur["v"] != value
        mine[key] = {"v": value, "ver": new_ver, "by": by, **({"pending": True} if ver is None else {})}  # None: gone
        callbacks = list(_listeners.get(plugin, {}).get(SHARE, ())) if moved else []
    if moved:
        _push({"type": "live_shared", "plugin": plugin, "key": key, "value": value, "by": by})
        for cb in callbacks:
            try:
                cb(key, value, by)
            except Exception:  # noqa: BLE001 — a plugin's callback never breaks delivery
                _log.warning("%s: live shared callback failed", plugin, exc_info=True)
    return new_ver


def _their_share(account: str, space: str, plugin: str, who: dict[str, str], data: Any) -> None:
    """A member's ask (the leader takes it in order and tells everyone), or the leader's value."""
    from backend.uefn_plugins import spaces

    if not isinstance(data, dict) or not _SHARE_KEY_RE.match(str(data.get("k") or "")):
        return
    key, value = str(data["k"]), data.get("v")
    st = room_of(plugin, account, space)
    if st is None:
        return
    if data.get("ask"):
        if not _leads_group(account, space, plugin, sender=who.get("device", "")):
            return  # only the leader orders the group's values
        by = {k: who.get(k, "") for k in ("name", "device")}
        ver = _take_share(plugin, key, value, by)
        try:
            spaces.provider().live_send(account, space, plugin, SHARE,
                                        {"k": key, "v": value, "ver": ver, "by": _wire_by(by)}, to="", fast=False)
        except Exception:  # noqa: BLE001 — the next resend carries it
            _log.debug("sharing %s of %s failed", key, plugin, exc_info=True)
        return
    mine = _my_group(plugin, account, space)
    if not mine or str(mine["hostDevice"]) != who.get("device", "") or mine["hostIsMe"]:
        return  # only the leader's word counts
    got = data.get("by") if isinstance(data.get("by"), dict) else {}
    device = str(got.get("device") or who.get("device", ""))
    if device == _device():
        by: dict[str, Any] = _my_who()
    else:
        name = str(got.get("name") or (who.get("name", "") if device == who.get("device") else "")) or "A teammate"
        by = {"name": name[:120], "device": device}
    try:
        ver = int(data.get("ver") or 0)
    except (TypeError, ValueError):
        return
    _take_share(plugin, key, value, by, ver=ver)


def shared(plugin: str) -> dict[str, dict[str, Any]]:
    """The group's values now: ``{key: {value, by}}`` (a page that just loaded)."""
    with _lock:
        return {k: {"value": s["v"], "by": dict(s["by"])} for k, s in (_shared.get(plugin) or {}).items()
                if s["v"] is not None}


def _share_tick(now: float) -> None:
    """The leader sends the group's values again, a key a message, for PCs that just joined."""
    from backend.uefn_plugins import spaces

    with _lock:
        plugins = [p for p in _shared if p in _live and now - _shared_sent.get(p, 0.0) >= STATE_EVERY_S]
    for plugin in plugins:
        st = _current(plugin)
        if st is None or _lead_of(plugin, st):
            continue  # a member: the leader sends them
        mine = _my_group(plugin, st.account, st.space)
        with _lock:
            _shared_sent[plugin] = now
            rows = list((_shared.get(plugin) or {}).items())
        if not mine or len(mine["members"]) < 2:
            continue
        for key, s in rows:
            try:
                spaces.provider().live_send(st.account, st.space, plugin, SHARE,
                                            {"k": key, "v": s["v"], "ver": s["ver"], "by": _wire_by(s["by"])}, to="",
                                            fast=True)
            except Exception:  # noqa: BLE001 — the next turn
                _log.debug("sending shared %s of %s failed", key, plugin, exc_info=True)


def groups(plugin: str, account: str, space: str) -> list[dict[str, Any]]:
    """The live groups of ``plugin`` in the space, from its last roster: ``room``, ``host``
    (name), ``hostDevice``, ``hostIsMe``, ``members`` (``name``, ``user``, ``device``,
    ``label``, ``me``, ``v``: its live version, ``older``: on an app that doesn't relay; the first to come
    first) and ``mine``. The host leads the group: the PC the room is named after while it is
    in it, else whoever has been in it longest, which every PC works out the same from the
    roster (no vote, no message); a PC on an older app leads only when nobody up to date is in
    it. This PC counts at once, before its next beat. Its own group first, then the biggest."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins.ledger import person
    from backend.uefn_plugins.vcs_engine import other_pc

    me = _device()
    roster = space_vcs.presence_get(account, space)["roster"]
    people = roster.get("people") if isinstance(roster, dict) else None
    rooms: dict[str, list[dict[str, Any]]] = {}
    for p in people if isinstance(people, list) else []:
        if not isinstance(p, dict) or str(p.get("device") or "") in ("", me):
            continue
        row = p.get("plugins", {}).get(plugin) if isinstance(p.get("plugins"), dict) else None
        live = row.get("live") if isinstance(row, dict) else None
        room = str(live.get("room") or "") if isinstance(live, dict) else ""
        if not room:
            continue
        try:
            since = float(live.get("since") or 0)
        except (TypeError, ValueError):
            since = 0.0
        pc = other_pc(account, space, p.get("user"), p["device"])  # this account on another PC
        rooms.setdefault(room, []).append({
            "name": f"You on {pc}" if pc else person(p.get("user_name"), p.get("user"), fallback="A teammate"),
            "user": str(p.get("user") or ""), "device": str(p["device"]), "label": str(p.get("label") or ""),
            "me": False, "since": since, "v": _version_of(live), "older": _older(p, live)})
    mine = room_of(plugin, account, space)
    if mine is not None:
        rooms.setdefault(mine["room"], []).append(
            {"name": "You", "user": "", "device": me, "label": "", "me": True, "since": mine["since"], "v": PROTOCOL,
             "older": False})
    return _shaped(rooms, mine["room"] if mine else "", _MEMBER_KEYS)


_MEMBER_KEYS = ("name", "user", "device", "label", "me", "since", "v", "older")


def _shaped(rooms: dict[str, list[dict[str, Any]]], mine: str, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Each room's group with its leader (:func:`groups`): this PC's first, then the biggest."""
    out = []
    for room, members in rooms.items():
        members.sort(key=lambda m: (m["since"], m["device"]))
        host_device = room[len(HOST_PREFIX):] if room.startswith(HOST_PREFIX) else ""
        leaders = [m for m in members if not m["older"]] or members  # an older app relays nothing
        host = next((m for m in leaders if m["device"] == host_device), leaders[0])
        out.append({"room": room, "host": host["name"], "hostDevice": host["device"], "hostIsMe": host["me"],
                    "members": [{k: m[k] for k in keys} for m in members], "mine": room == mine})
    out.sort(key=lambda g: (not g["mine"], -len(g["members"]), g["host"].lower()))
    return out


def team_groups(account: str, space: str) -> list[dict[str, Any]]:
    """The live groups on a team copy, every plugin together (:func:`groups` has one
    plugin's), worked out the same way. A teammate's room is what their presence says at
    its top; an app from before one Live covered every plugin says it per plugin only (its
    newest counts, and ``partial`` says so). Members also carry ``plugin``: the plugin they
    have on screen ('' for none)."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins.ledger import person
    from backend.uefn_plugins.vcs_engine import other_pc

    me = _device()
    roster = space_vcs.presence_get(account, space)["roster"]
    people = roster.get("people") if isinstance(roster, dict) else None
    rooms: dict[str, list[dict[str, Any]]] = {}
    for p in people if isinstance(people, list) else []:
        if not isinstance(p, dict) or str(p.get("device") or "") in ("", me):
            continue
        rows = p.get("plugins") if isinstance(p.get("plugins"), dict) else {}
        top = p.get("live")
        live = top if isinstance(top, dict) and top.get("room") else None
        partial = live is None
        if live is None:
            per = [r["live"] for r in rows.values()
                   if isinstance(r, dict) and isinstance(r.get("live"), dict) and r["live"].get("room")]
            live = max(per, key=_since_of, default=None)
        if live is None:
            continue
        viewing = next((str(pid) for pid, r in sorted(rows.items()) if isinstance(r, dict) and r.get("viewing")), "")
        pc = other_pc(account, space, p.get("user"), p["device"])  # this account on another PC
        rooms.setdefault(str(live["room"]), []).append({
            "name": f"You on {pc}" if pc else person(p.get("user_name"), p.get("user"), fallback="A teammate"),
            "user": str(p.get("user") or ""), "device": str(p["device"]), "label": str(p.get("label") or ""),
            "me": False, "since": _since_of(live), "v": _version_of(live), "older": _older(p, live),
            "plugin": viewing, "partial": partial})
    with _lock:
        team = _teams.get((account, space))
    if team is not None:
        rooms.setdefault(team.room, []).append(
            {"name": "You", "user": "", "device": me, "label": "", "me": True, "since": team.since, "v": PROTOCOL,
             "older": False, "plugin": "", "partial": False})
    return _shaped(rooms, team.room if team else "", (*_MEMBER_KEYS, "plugin", "partial"))


def _since_of(row: dict[str, Any]) -> float:
    try:
        return float(row.get("since") or 0)
    except (TypeError, ValueError):
        return 0.0


def _older(person: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether a teammate's app is from before groups ran through a leader. Its live row's
    ``v`` says it is up to date; else its app version (``ver``, which the team service adds
    to presence) says; a PC that says neither (1.2.434) is taken as relaying, unless what it
    sends gave it away (:func:`_note_legacy`)."""
    from backend.uefn_plugins.plugin_version import parse_plugin_version

    try:
        if _version_of(row) >= RELAY_V:
            return False
    except (TypeError, ValueError):
        pass
    ver = parse_plugin_version(person.get("ver"))
    if ver != (0, 0, 0):
        return ver < RELAY_SINCE
    return str(person.get("device") or "") in _legacy


def _version_of(row: dict[str, Any]) -> int:
    """The live version a presence row says (0: none, an app before 1.2.435)."""
    try:
        return int(row.get("v") or 0)
    except (TypeError, ValueError):
        return 0


def _note_legacy(account: str, space: str, plugin: str, who: dict[str, str], topic: str, data: Any) -> None:
    """A tab click with no origin, or a change saying nothing of who made it: only an app
    from before groups ran through a leader sends those. The bar and the group's leader
    learn it at once (its presence may not say its version yet)."""
    device = who.get("device", "")
    if not device or device in _legacy or not isinstance(data, dict):
        return
    if not ((topic == NAV and not data.get("o")) or (topic == DATA and "by" not in data)):
        return
    with _lock:
        if len(_legacy) >= LEGACY_MAX:
            _legacy.clear()
        _legacy.add(device)
    peers_changed(account, space, plugin)


def status(plugin: str, *, come_along: bool = True) -> dict[str, Any]:
    """What the bar shows: ``supported``, ``on``, ``room``, ``state``, ``peers`` (name, user,
    device, label, path: ``direct`` | ``forwarded`` | ``none``), ``groups`` and ``rejoin``
    (what this PC goes back into on the team copy while not live: ``start``, a teammate's
    room, or ''). A plugin of a team copy this PC is live on joins its group as it is read
    (``come_along``: switched to that copy, enabled, opened)."""
    from backend.uefn_plugins import spaces

    ok, why = supported(plugin)
    st = _current(plugin)  # a plugin no longer on that team copy leaves its room here
    if st is not None and not ok:
        leave(plugin)
        st = None
    if st is None and ok and come_along:
        st = _come_along(plugin)
    out: dict[str, Any] = {"supported": ok, "reason": why, "on": st is not None,
                           "room": st.room if st else "", "state": "off", "peers": [], "groups": [], "rejoin": ""}
    team = _team(plugin) if ok else None
    if team is not None:
        try:
            out["groups"] = groups(plugin, *team)
        except Exception:  # noqa: BLE001 — no list this read
            _log.debug("live groups of %s failed", plugin, exc_info=True)
        try:
            out["rejoin"] = _remembered_team(*team, team_groups(*team))
        except Exception:  # noqa: BLE001
            _log.debug("live rejoin of %s failed", plugin, exc_info=True)
    if st is None:
        return out
    try:
        got = spaces.provider().live_state(st.account, st.space, plugin) or {}
    except Exception:  # noqa: BLE001 — live, peers unknown for now
        got = {}
    out["state"] = str(got.get("state") or "live")
    out["peers"] = [dict(p) for p in got.get("peers") or [] if isinstance(p, dict)][:32]
    out["connecting"], out["unlinked"] = _unlinked(st, out["peers"], out["groups"])
    return out


def _come_along(plugin: str) -> _State | None:
    """A plugin of a team copy this PC is live on that isn't in its group (switched to that
    copy, enabled, opened) joins it now."""
    team = _team(plugin)
    with _lock:
        if team is None or team not in _teams or plugin in _own:
            return None
    _reconcile(*team)
    with _lock:
        return _live.get(plugin)


def team_status(account: str, space: str) -> dict[str, Any]:
    """What the title bar's Live menu shows of a team copy: ``on``, ``room``, ``since``,
    ``groups`` (:func:`team_groups`), ``plugins`` (this PC's in the group), ``state``,
    ``connecting`` / ``unlinked`` (by name: nothing reaches them yet / any more) and
    ``rejoin``."""
    from backend.uefn_plugins import spaces

    with _lock:
        team = _teams.get((account, space))
        plugins = sorted(p for p, st in _live.items() if (st.account, st.space) == (account, space) and p not in _own)
    out: dict[str, Any] = {"on": team is not None, "room": team.room if team else "",
                           "since": team.since if team else 0.0, "groups": [], "plugins": plugins,
                           "state": "off", "connecting": [], "unlinked": [], "rejoin": "", "online": []}
    try:
        out["groups"] = team_groups(account, space)
    except Exception:  # noqa: BLE001 — no list this read
        _log.debug("live groups of %s failed", space, exc_info=True)
    try:
        out["online"] = online(account, space, out["groups"])
    except Exception:  # noqa: BLE001 — no list this read
        _log.debug("live online of %s failed", space, exc_info=True)
    if team is None:
        out["rejoin"] = _remembered_team(account, space, out["groups"])
        return out
    peers: dict[str, dict[str, Any]] = {}
    states: list[str] = []
    for plugin in plugins:
        try:
            got = spaces.provider().live_state(account, space, plugin) or {}
        except Exception:  # noqa: BLE001 — live, peers unknown for now
            got = {}
        states.append(str(got.get("state") or "live"))
        for p in got.get("peers") or []:
            if isinstance(p, dict) and (str(p.get("device") or "") not in peers or p.get("path") in ("direct", "forwarded")):
                peers[str(p.get("device") or "")] = dict(p)
    out["state"] = "live" if "live" in states or not states else states[0]
    out["connecting"], out["unlinked"] = _unlinked(_State(account, space, team.room, team.since),
                                                   list(peers.values()), out["groups"])
    return out


def overview() -> dict[str, Any]:
    """Every team copy this PC has plugins on, for the title bar's Live menu: ``teams``
    (:func:`team_status` with ``account``, ``space``, ``label``, ``supported`` and ``reason``;
    members also carry ``pluginLabel``), live ones first, and ``on``: live on one of them."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes

    from backend.uefn_plugins.spaces import plugin_label

    account = scopes.account_id()
    labels: dict[str, str] = {}

    def label_of(plugin: str) -> str:
        if plugin not in labels:
            labels[plugin] = plugin_label(plugin) or plugin
        return labels[plugin]

    teams = []
    for space in sorted(set(repo.links(account).values())):
        plugins, why = _team_plugins(account, space)
        row = {"account": account, "space": space, "label": str(repo.sync_get(account, space).get("label") or "") or "Team",
               "supported": bool(plugins), "reason": "" if plugins else why, **team_status(account, space)}
        for g in row["groups"]:
            for m in g["members"]:
                m["pluginLabel"] = label_of(m["plugin"]) if m.get("plugin") else ""
        teams.append(row)
    teams.sort(key=lambda t: (not t["on"], t["label"].lower()))
    return {"ok": True, "on": any(t["on"] for t in teams), "teams": teams}


# --------------------------------------------------------------------------- invites
#
# Teammates with the app open (the team copy's presence roster) and not in this PC's group
# show in the Live menu with Invite. An invite rides this PC's presence (``invites``: to,
# room, at) and a forced beat; the team channel wakes the teammate's PC at once, its roster
# carries the invite, and :func:`roster_invites` pops a Join question there for
# :data:`INVITE_S`.


def online(account: str, space: str, groups_: list[dict[str, Any]] | None = None,
           *, now: float | None = None) -> list[dict[str, Any]]:
    """Teammates with the app open on the team copy who aren't in this PC's group: ``name``,
    ``user``, ``device``, ``live`` (in another group) and ``invited`` (an invite still
    stands)."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins.ledger import person
    from backend.uefn_plugins.vcs_engine import other_pc

    now = time.time() if now is None else now
    groups_ = team_groups(account, space) if groups_ is None else groups_
    mine = next((g for g in groups_ if g.get("mine")), None)
    with_me = {str(m.get("device") or "") for m in (mine or {}).get("members") or []}
    live_somewhere = {str(m.get("device") or "") for g in groups_ for m in g.get("members") or []}
    with _lock:
        sent = {d: at for d, (_, at) in _invites.get((account, space), {}).items() if now - at < INVITE_S}
    roster = space_vcs.presence_get(account, space)["roster"]
    people = roster.get("people") if isinstance(roster, dict) else None
    me, out, seen = _device(), [], set()
    for p in people if isinstance(people, list) else []:
        device = str(p.get("device") or "") if isinstance(p, dict) else ""
        if not device or device == me or device in with_me or device in seen:
            continue
        seen.add(device)
        pc = other_pc(account, space, p.get("user"), device)
        out.append({"name": f"You on {pc}" if pc else person(p.get("user_name"), p.get("user"), fallback="A teammate"),
                    "user": str(p.get("user") or ""), "device": device,
                    "live": device in live_somewhere, "invited": device in sent})
    out.sort(key=lambda r: r["name"].lower())
    return out


def invite(account: str, space: str, device: str) -> dict[str, Any]:
    """Ask a teammate's PC into this PC's group on the team copy: it gets a Join question
    for :data:`INVITE_S`. Not live yet: this PC starts live first (the group it lands in is
    the one offered)."""
    device = str(device or "")
    if not device or device == _device() or not _ROOM_RE.match(device):
        return {"ok": False, "error": "Pick a teammate to invite."}
    with _lock:
        team = _teams.get((account, space))
    if team is None:
        got = join_team(account, space)
        if not got.get("ok"):
            return got
        remember_team(account, space, START)
        with _lock:
            team = _teams.get((account, space))
        if team is None:
            return {"ok": False, "error": "Couldn't go live."}
    now = time.time()
    with _lock:
        sent = _invites.setdefault((account, space), {})
        for d in [d for d, (_, at) in sent.items() if now - at >= INVITE_S]:
            sent.pop(d)
        if device not in sent and len(sent) >= INVITES_MAX:
            return {"ok": False, "error": f"At most {INVITES_MAX} invites at once. Try again in a moment."}
        sent[device] = (team.room, now)
    _beat_soon(account, space)
    return {"ok": True, "expires": now + INVITE_S, **team_status(account, space)}


def invites_of(account: str, space: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """The invites this PC's presence carries now: ``to``, ``room``, ``at``."""
    now = time.time() if now is None else now
    with _lock:
        sent = _invites.get((account, space), {})
        return [{"to": d, "room": room, "at": at} for d, (room, at) in sorted(sent.items())
                if now - at < INVITE_S][:INVITES_MAX]


def roster_invites(account: str, space: str, roster: Any, *, now: float | None = None) -> list[dict[str, Any]]:
    """A roster came in: an invite a teammate's presence addresses to this PC, still standing
    and into a group this PC isn't in, pops a Join question (``live_invite``: account, space,
    room, ``from``, ``expires`` in this PC's clock). Each invite once. Returns what was told."""
    from backend.uefn_plugins.ledger import person

    now = time.time() if now is None else now
    me = _device()
    with _lock:
        team = _teams.get((account, space))
    told: list[dict[str, Any]] = []
    people = roster.get("people") if isinstance(roster, dict) else None
    for p in people if isinstance(people, list) else []:
        device = str(p.get("device") or "") if isinstance(p, dict) else ""
        rows = p.get("invites") if device and device != me else None
        for inv in rows[:INVITES_MAX] if isinstance(rows, list) else ():
            if not isinstance(inv, dict) or str(inv.get("to") or "") != me:
                continue
            room = str(inv.get("room") or "")
            try:
                at = float(inv.get("at") or 0)
            except (TypeError, ValueError):
                continue
            age = max(0.0, now - at)  # a clock ahead of this one counts as just sent
            if age >= INVITE_S or not room.startswith(HOST_PREFIX) or not _ROOM_RE.match(room):
                continue
            if team is not None and team.room == room:
                continue
            with _lock:
                if (device, at) in _told_invites:
                    continue
                if len(_told_invites) >= LEGACY_MAX:
                    _told_invites.clear()
                _told_invites.add((device, at))
            event = {"type": "live_invite", "account": account, "space": space, "room": room,
                     "from": person(p.get("user_name"), p.get("user"), fallback="A teammate"),
                     "expires": now + (INVITE_S - age)}
            told.append(event)
            _push(event)
    return told


def _unlinked(st: _State, peers: list[dict[str, Any]], groups_: list[dict[str, Any]],
              now: float | None = None) -> tuple[list[str], list[str]]:
    """Who in this PC's group the provider has no link to: ``(connecting, unlinked)`` by
    name. Presence can show someone in the group while nothing reaches them; past the
    grace they are unreachable, so the bar says so instead of showing nothing."""
    now = time.time() if now is None else now
    linked = {str(p.get("device") or "") for p in peers if p.get("path") in ("direct", "forwarded")}
    mine = next((g for g in groups_ if g.get("mine")), None)
    connecting: list[str] = []
    unlinked: list[str] = []
    for m in (mine or {}).get("members") or []:
        if m.get("me") or not m.get("device") or m["device"] in linked:
            continue
        both_since = max(st.since, float(m.get("since") or 0))
        (connecting if now - both_since < LINK_GRACE_S else unlinked).append(str(m.get("name") or "A teammate"))
    return connecting, unlinked


# --------------------------------------------------------------------------- holds


def hold(plugin: str, anchor: str, on: bool, t: float = 0.0, view: str = "") -> dict[str, Any]:
    """This PC presses (``on``) or lets go of the control at ``anchor`` (the page's live script,
    pressed at ``t`` ms, in ``view``): the group hears it at once, every teammate with this
    PC's next presence. Only on a team copy."""
    anchor, view = str(anchor or ""), str(view or "")
    if not _ANCHOR_RE.match(anchor) or not _VIEW_RE.match(view):
        return {"ok": False, "error": "A hold names an anchor (up to 400 characters) and a view id."}
    if _team(plugin) is None:
        return {"ok": False, "error": "Holds are for a team copy."}
    try:
        t = float(t or 0.0)
    except (TypeError, ValueError):
        t = 0.0
    with _lock:
        mine = _holds.setdefault(plugin, {})
        if on:
            mine[anchor] = (t, view, time.time())
        else:
            mine.pop(anchor, None)
        let_go = not on and not mine
    if is_live(plugin):
        send(plugin, HOLD, {"anchor": anchor, "on": bool(on), "t": t, "v": view})
    if let_go:
        _reload_kept(plugin)  # a teammate's pull waited for this PC to let go: its panels show it now
    return {"ok": True}


def spaces_holding(*, now: float | None = None) -> set[tuple[str, str]]:
    """``(account, space)`` of every team copy this PC holds a control in now: its presence
    carries the holds, so the scheduler keeps beating there (live or not)."""
    now = time.time() if now is None else now
    with _lock:
        plugins = [p for p, mine in _holds.items() if any(now - at <= HOLD_STALE_S for _, _, at in mine.values())]
    return {team for team in map(_team, plugins) if team is not None}


def holds_of(plugin: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """What this PC holds in ``plugin`` (for its presence): ``a`` anchor, ``t``, ``v``."""
    now = time.time() if now is None else now
    with _lock:
        mine = _holds.get(plugin) or {}
        for anchor in [a for a, (_, _, at) in mine.items() if now - at > HOLD_STALE_S]:
            mine.pop(anchor, None)
        rows = sorted(mine.items(), key=lambda kv: kv[1][0])[:HOLDS_MAX]
    return [{"a": a, "t": t, "v": v} for a, (t, v, _) in rows]


def roster_holds(account: str, space: str, roster: Any) -> None:
    """A roster came in (a beat or heads): each plugin's panels get what teammates hold in it
    (``live_holds``: device, name, anchor, ``t``, ``v``), when that changed."""
    from backend.uefn_plugins.ledger import person

    me = _device()
    found: dict[str, list[dict[str, Any]]] = {}
    people = roster.get("people") if isinstance(roster, dict) else None
    for p in people if isinstance(people, list) else []:
        device = str(p.get("device") or "") if isinstance(p, dict) else ""
        plugins = p.get("plugins") if device and device != me else None
        for plugin, row in plugins.items() if isinstance(plugins, dict) else ():
            for h in (row.get("holds") or [])[:HOLDS_MAX] if isinstance(row, dict) else ():
                if isinstance(h, dict) and isinstance(h.get("a"), str) and _ANCHOR_RE.match(h["a"]):
                    found.setdefault(str(plugin), []).append({
                        "device": device, "name": person(p.get("user_name"), p.get("user"), fallback="A teammate"),
                        "anchor": h["a"], "t": h.get("t") or 0, "v": str(h.get("v") or "")})
    with _lock:
        told = {p: v for (s, p), v in _told_holds.items() if s == space}
        moved = [p for p in set(told) | set(found) if told.get(p, []) != found.get(p, [])]
        for plugin in moved:
            _told_holds[(space, plugin)] = found.get(plugin, [])
    for plugin in moved:
        _push({"type": "live_holds", "plugin": plugin, "holds": found.get(plugin, [])})


def holds_now(plugin: str) -> list[dict[str, Any]]:
    """What the panels were last told teammates hold in ``plugin`` (a page that just loaded)."""
    team = _team(plugin)
    with _lock:
        return list(_told_holds.get((team[1], plugin), [])) if team else []


# --------------------------------------------------------------------------- changes


def _pull_now(account: str, space: str, plugin: str) -> None:
    try:
        from backend.uefn_plugins import sync_scheduler

        sync_scheduler.wake(account, space, plugin)
    except Exception:  # noqa: BLE001 — the next round brings it
        _log.debug("live wake failed", exc_info=True)


def committed(account: str, space: str, plugin: str, *, throttle: bool = False) -> None:
    """This PC's changes to a team copy were just committed: its live group pulls them now.
    Sent as each changeset commits (``throttle``: at most one each :data:`NOTE_GAP_S`), and
    always once more when the round is done, so the last of them is never left unsaid."""
    if room_of(plugin, account, space) is None:
        return
    now = time.monotonic()
    with _lock:
        if throttle and now - _noted.get((account, space, plugin), float("-inf")) < NOTE_GAP_S:
            return
        _noted[(account, space, plugin)] = now
    try:
        from backend.uefn_plugins import spaces

        spaces.provider().live_send(account, space, plugin, CHANGE, {}, to="", fast=False)
    except Exception:  # noqa: BLE001 — their next round brings it
        _log.debug("live change note of %s failed", plugin, exc_info=True)


# --------------------------------------------------------------------------- from the provider


def deliver(account: str, space: str, plugin: str, sender: dict[str, Any], topic: str, data: Any) -> None:
    """A message from a teammate live in the same room (the provider opened and checked it)."""
    st = room_of(plugin, account, space)
    if st is None:
        return
    who = {k: str(sender.get(k) or "") for k in ("name", "user", "device", "label")}
    _note_legacy(account, space, plugin, who, topic, data)
    if topic == DATA:  # by the person's own name: a change this PC sends for them says who made it
        _apply_data(account, space, plugin, who, data)
        return
    if topic == STATE:
        _their_state(plugin, who, data)
        return
    if topic == SHARE:
        _their_share(account, space, plugin, who, data)
        return
    try:
        from backend.uefn_plugins.vcs_engine import other_pc

        pc = other_pc(account, space, who["user"], who["device"])
    except Exception:  # noqa: BLE001 — their own name then
        pc = ""
    if pc:  # this account on another PC: pointers say which PC, not the same name twice
        who["name"] = f"You on {pc}"
    if topic == CHANGE:
        _pull_now(account, space, plugin)
        return
    _push({"type": "live_message", "plugin": plugin, "from": who, "topic": topic, "data": data})
    with _lock:
        callbacks = list(_listeners.get(plugin, {}).get(topic, ()))
    for cb in callbacks:
        try:
            cb(who, data)
        except Exception:  # noqa: BLE001 — a plugin's callback never breaks delivery
            _log.warning("%s: live callback for %s failed", plugin, topic, exc_info=True)


# --------------------------------------------------------------------------- live data
#
# A plugin with "data_live": while this PC is live in a group, every change it makes to the
# plugin's docs and files (a panel's, the person's, an agent's in the MCP bridge: they all
# land as rows here) goes to the group at once, and a teammate's lands here at once. Inside
# the group the newest change of an item wins and nobody locks anyone out
# (scopes._team_write).
#
# The group syncs through its leader (the host, :func:`groups`), like a document server:
# the leader takes each groupmate's change on as its own to send, naming who made it
# (``author``), and the others land it as a pulled copy. A groupmate sends nothing of what it
# changed since it joined while the leader is in reach (:func:`hold_since`); the leader's
# commit coming down finds the same content and settles it. One of its own older versions
# coming back never undoes a newer edit (:func:`own_echo`), and when the group passes to
# another PC what the old leader hasn't sent goes to the new one (:func:`send_changes`). A
# change the leader hasn't sent by :data:`HOLD_MAX_S`, or once the leader is out of reach or
# this PC left, goes out from here as before: at worst it is sent twice, never lost.
# Teammates outside the group get everything with sync.


def _publish_room(plugin: str, st: _State | None) -> None:
    """Where ``plugin`` is live here, for every process (an agent's write in the MCP bridge
    takes no lock in the group either)."""
    import os

    from backend.store.repos import kv

    try:
        if st is None:
            kv.delete_doc("workspace_state", ROOM_KEY + plugin)
        else:
            kv.set_doc("workspace_state", ROOM_KEY + plugin, {"account": st.account, "space": st.space,
                                                              "room": st.room, "pid": os.getpid()})
    except Exception:  # noqa: BLE001 — the app process still knows; others lock as before
        _log.debug("publishing the live room of %s failed", plugin, exc_info=True)


def room_anywhere(plugin: str, account: str, space: str) -> str:
    """The room ``plugin`` is live in on this PC for that team copy, from any process ('' when
    not live). A room left by an app that is gone (it crashed) doesn't count."""
    here = room_of(plugin, account, space)
    if here is not None:
        return str(here["room"])
    import os

    from backend.store.repos import kv

    doc = kv.get_doc("workspace_state", ROOM_KEY + plugin)
    if not isinstance(doc, dict) or (doc.get("account"), doc.get("space")) != (account, space):
        return ""
    pid = int(doc.get("pid") or 0)
    if pid == os.getpid() or not pid:
        return ""  # this process is the app and isn't live there
    try:
        import psutil

        alive = psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001
        alive = False
    return str(doc.get("room") or "") if alive else ""


def group_devices(plugin: str, account: str, space: str) -> set[str] | None:
    """The other PCs live in this PC's group for ``plugin`` (from the team's last roster),
    or None when this PC isn't live there."""
    room = room_anywhere(plugin, account, space)
    if not room:
        return None
    from backend.store.repos import space_vcs

    roster = space_vcs.presence_get(account, space)["roster"]
    people = roster.get("people") if isinstance(roster, dict) else None
    me = _device()
    out: set[str] = set()
    for p in people if isinstance(people, list) else []:
        row = (p.get("plugins") or {}).get(plugin) if isinstance(p, dict) and isinstance(p.get("plugins"), dict) else None
        live = row.get("live") if isinstance(row, dict) else None
        device = str(p.get("device") or "") if isinstance(p, dict) else ""
        if device and device != me and isinstance(live, dict) and live.get("room") == room:
            out.add(device)
    return out


def _outbox_start() -> None:
    with _lock:
        if _outbox and _outbox[0].is_alive():
            return
        thread = threading.Thread(target=_outbox_loop, name="live-outbox", daemon=True)
        _outbox[:] = [thread]
    thread.start()


def _outbox_loop() -> None:
    while True:
        time.sleep(OUTBOX_EVERY_S)
        with _lock:
            if not _live:
                _outbox.clear()
                return
        try:
            send_changes()
        except Exception:  # noqa: BLE001 — the next turn tries again; sync carries it anyway
            _log.debug("sending live changes failed", exc_info=True)
        try:
            _state_tick()
        except Exception:  # noqa: BLE001 — the next turn tries again
            _log.debug("live states failed", exc_info=True)


def carried(kind: str, size: int, *, deleted: bool = False) -> bool:
    """Whether a change goes to the live group with its content (a delete always does): a
    doc or file bigger than that only says it changed, so the group's leader can't send it
    for this PC and the group pulls it once this PC has (:func:`hold_since` never holds it)."""
    if deleted:
        return True
    if kind == "doc":
        return size <= DATA_INLINE_MAX
    return kind == "asset" and size <= DATA_INLINE_MAX * 3 // 4


def _data_item(account: str, space: str, plugin: str, r: dict[str, Any]) -> dict[str, Any] | None:
    """One written row as a live message: the doc or file itself when it is small enough,
    else just what changed (the group pulls it)."""
    import base64

    from backend.uefn_plugins import data_crypto, scopes

    item: dict[str, Any] = {"k": r["kind"], "key": r["key"], "at": float(r["updated"]), "sha": str(r["sha256"] or ""),
                            "op": "del" if int(r["deleted"]) else "put"}
    if item["op"] == "del" or r["kind"] not in ("doc", "asset"):
        return item
    try:
        if r["kind"] == "doc" and r["value"] is not None:
            raw = data_crypto.open_text(r["value"], account)
            if carried("doc", len(raw.encode("utf-8"))):
                item["v"] = raw
        elif r["kind"] == "asset" and carried("asset", int(r["size"] or 0)):
            sealed = scopes.asset_file(scopes.shared_scope(account, space), plugin, r["key"]).read_bytes()
            item["b"] = base64.b64encode(data_crypto.open_bytes(sealed, account)).decode("ascii")
    except Exception:  # noqa: BLE001 — sent without its content: the group pulls it
        _log.debug("reading %s %s for the live group failed", r["kind"], r["key"], exc_info=True)
    return item


def _my_group(plugin: str, account: str, space: str) -> dict[str, Any] | None:
    try:
        return next((g for g in groups(plugin, account, space) if g["mine"]), None)
    except Exception:  # noqa: BLE001 — no roster to tell: as if alone
        _log.debug("live groups of %s failed", plugin, exc_info=True)
        return None


def _in_reach(account: str, space: str, plugin: str, device: str) -> bool:
    from backend.uefn_plugins import spaces

    try:
        got = spaces.provider().live_state(account, space, plugin) or {}
    except Exception:  # noqa: BLE001 — unsure: not in reach
        return False
    return any(isinstance(p, dict) and str(p.get("device") or "") == device and p.get("path") in ("direct", "forwarded")
               for p in got.get("peers") or [])


def hold_since(account: str, space: str, plugin: str) -> float | None:
    """When this PC joined its live group of ``plugin``, while a groupmate leads it and is in
    reach: what this PC changed since then goes to the leader, which sends it (the version
    engine holds it back up to :data:`HOLD_MAX_S`; never one too big to go live,
    :func:`carried`). None: this PC sends its own."""
    from backend.uefn_plugins import scopes

    st = room_of(plugin, account, space)
    if st is None or not scopes.data_live(plugin):
        return None
    mine = _my_group(plugin, account, space)
    if mine is None or mine["hostIsMe"] or len(mine["members"]) < 2:
        return None
    leader = next((m for m in mine["members"] if m["device"] == mine["hostDevice"]), {})
    if int(leader.get("v") or 0) < DATA_V and not scopes.data_live_asked(plugin):
        return None  # its app sends only the data of plugins that ask for it live: this one's goes from here
    return float(st["since"]) if _in_reach(account, space, plugin, str(mine["hostDevice"])) else None


def leads(account: str, space: str, plugin: str, sender: str = "") -> bool:
    """This PC leads its live group of a ``data_live`` plugin, with ``sender`` in it (as the
    roster shows): groupmates' changes are its to send. Someone the roster doesn't show in
    the group yet sends their own."""
    from backend.uefn_plugins import scopes

    return scopes.data_live(plugin) and _leads_group(account, space, plugin, sender)


def _leads_group(account: str, space: str, plugin: str, sender: str = "") -> bool:
    """This PC leads its live group of ``plugin``, with ``sender`` in it as the roster shows. A
    groupmate on an older app sends its own (it never holds anything for a leader)."""
    if room_of(plugin, account, space) is None:
        return False
    mine = _my_group(plugin, account, space)
    devices = {str(m["device"]) for m in (mine or {}).get("members") or [] if not m["me"] and not m["older"]}
    return bool(mine and mine["hostIsMe"] and devices and (not sender or sender in devices))


def own_echo(account: str, space: str, plugin: str, kind: str, key: str, sha: str) -> bool:
    """``sha`` is a version of the item this PC sent its group earlier (its leader committed
    it): coming down, it only moves the base on, under the newer edit made here since."""
    with _lock:
        return bool(sha) and sha in _sent_shas.get((account, space, plugin, kind, key), ())


def relayed(account: str, space: str, plugin: str, kind: str, key: str, sha: str) -> dict[str, str] | None:
    """Who made a groupmate's change this PC sends as its group's leader (``author``,
    ``author_pc``, ``actor``, ``ducky``), while the row still has that content."""
    with _lock:
        got = _relayed.get((account, space, plugin, kind, key))
    return dict(got) if got and got.get("sha") == sha else None


def _made_by(account: str, space: str, plugin: str, kind: str, key: str) -> dict[str, str]:
    """Who changed the item here last (the person, or one of their duckies)."""
    from backend.store.repos import space_vcs

    unit = f"{kind}:{key}"
    try:
        for act in space_vcs.activity(account, space):
            if act["plugin_id"] == plugin and act["unit"] == unit:
                return {"actor": "agent" if act["actor"] == "agent" else "user", "ducky": str(act["ducky"] or "")}
    except Exception:  # noqa: BLE001 — the person then
        _log.debug("activity of %s failed", unit, exc_info=True)
    return {"actor": "user", "ducky": ""}


def send_changes() -> int:
    """This PC's writes since the last turn, to each live group of a ``data_live`` plugin,
    each saying who made it here. When the group passed to another leader, what is still
    unsent here since joining goes again (the new leader sends it). A groupmate's change this
    PC took on as leader isn't sent back. Returns how many went."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes, spaces

    with _lock:
        live_now = list(_live.items())
    sent = 0
    for plugin, st in live_now:
        if not scopes.data_live(plugin):
            continue
        mark = (st.account, st.space, plugin)
        mine = _my_group(plugin, st.account, st.space)
        lead = str(mine["hostDevice"]) if mine and len(mine["members"]) > 1 else ""
        with _lock:
            if lead and _led_by.get(plugin, lead) != lead:
                _marks[mark] = st.since  # the new leader gets what the old one didn't send
            if lead:
                _led_by[plugin] = lead
            since = _marks.setdefault(mark, st.since)  # what was written before joining goes with sync
        for r in repo.written_since(st.account, st.space, plugin, since):
            item_key = (st.account, st.space, plugin, r["kind"], r["key"])
            with _lock:
                theirs = (_relayed.get(item_key) or {}).get("sha") == r["sha256"]
            if not scopes.is_local_key(plugin, r["key"]) and not theirs:
                item = _data_item(st.account, st.space, plugin, r)
                if item is not None:
                    item["by"] = _made_by(st.account, st.space, plugin, r["kind"], r["key"])
                    with _lock:
                        q = _sent_shas.setdefault(item_key, collections.deque(maxlen=ECHO_KEEP))
                        if item["sha"] and item["sha"] not in q:
                            q.append(item["sha"])
                    spaces.provider().live_send(st.account, st.space, plugin, DATA, item, to="", fast=False)
                    sent += 1
            with _lock:
                _marks[mark] = max(_marks.get(mark, 0.0), float(r["updated"]))
    return sent


def _note_relayed(account: str, space: str, plugin: str, kind: str, key: str, sha: str, who: dict[str, str],
                  by: dict[str, Any]) -> None:
    """The leader takes a groupmate's change on: its changeset names them, their PC and
    (for one of their duckies) the ducky."""
    with _lock:
        if len(_relayed) > 5000:
            _relayed.clear()
        _relayed[(account, space, plugin, kind, key)] = {
            "sha": sha, "author": str(who.get("name") or "")[:120], "author_pc": str(who.get("label") or "")[:64],
            "actor": "agent" if by.get("actor") == "agent" else "user", "ducky": str(by.get("ducky") or "")[:80]}


def _apply_data(account: str, space: str, plugin: str, who: dict[str, str], data: Any) -> bool:
    """A groupmate's change: it lands here as a pulled copy would, unless this PC changed the
    same item later (the newest change wins; this PC's goes to them). On the group's leader it
    lands as a change of its own to send, naming who made it. Returns whether it landed."""
    import base64
    import hashlib

    from backend.store import db
    from backend.store.repos import plugin_data as repo
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import data_crypto, scopes

    if not scopes.data_live(plugin) or not isinstance(data, dict):
        return False
    kind, key, op = str(data.get("k") or ""), str(data.get("key") or ""), str(data.get("op") or "")
    valid = scopes.valid_doc_key(key) if kind == "doc" else scopes.valid_asset_path(key) if kind == "asset" else False
    if not valid or op not in ("put", "del") or scopes.is_local_key(plugin, key):
        return False
    try:
        at = float(data.get("at") or 0.0)
    except (TypeError, ValueError):
        return False
    cur = repo.get(account, space, plugin, kind, key)
    if cur is not None and int(cur["sensitive"]):
        return False
    if cur is not None and int(cur["dirty"]) and (float(cur["updated"]), _device()) > (at, who.get("device", "")):
        return False  # changed here later: mine wins, and goes to them
    sha = str(data.get("sha") or "")
    mine = leads(account, space, plugin, sender=str(who.get("device") or ""))  # the leader sends it for them
    by = data.get("by") if isinstance(data.get("by"), dict) else {}
    if op == "put":
        if cur is not None and not int(cur["deleted"]) and cur["sha256"] == sha:
            return False  # here already (a groupmate who still holds it sends it after HOLD_MAX_S)
        payload = data.get("v") if kind == "doc" else data.get("b")
        if not isinstance(payload, str):
            _pull_now(account, space, plugin)  # too big to carry: sync brings it (they send it themselves)
            return False
        try:
            raw = payload.encode("utf-8") if kind == "doc" else base64.b64decode(payload, validate=True)
        except ValueError:
            return False
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) > (scopes.DOC_MAX_BYTES if kind == "doc" else scopes.ASSET_MAX_BYTES):
            return False
        if mine:
            _note_relayed(account, space, plugin, kind, key, sha, who, by)
        if kind == "doc":
            conn = db.connect()
            with db.write_txn(conn):
                repo.upsert(conn, account, space, plugin, "doc", key, value=data_crypto.seal_text(payload, account),
                            size=len(raw), sha256=sha, dirty=mine)
                space_vcs.change_log_bump(conn, plugin, [key])
        else:
            scopes._write_asset(scopes.shared_scope(account, space), plugin, key, data_crypto.seal_bytes(raw, account),
                                size=len(raw), sha256=sha, dirty=mine)
            conn = db.connect()
            with db.write_txn(conn):
                space_vcs.change_log_bump(conn, plugin, [key])
    else:
        if cur is None or int(cur["deleted"]):
            return False
        target = scopes.asset_file(scopes.shared_scope(account, space), plugin, key) if kind == "asset" else None
        if mine:
            _note_relayed(account, space, plugin, kind, key, str(cur["sha256"] or ""), who, by)
            repo.remove(account, space, plugin, kind, key, tombstone=True, target_path=target)
            conn = db.connect()
            with db.write_txn(conn):
                space_vcs.change_log_bump(conn, plugin, [key])
        else:
            conn = db.connect()
            with db.write_txn(conn):
                repo.delete_row(conn, account, space, plugin, kind, key)
                space_vcs.change_log_bump(conn, plugin, [key])
            if target is not None:
                target.unlink(missing_ok=True)
    scopes.deliver_data_changes(plugin)  # the plugin's backend hears it like a pull
    _push({"type": "plugin_scope_changed", "plugins": [plugin], "synced": True, "keys": {plugin: [key]}})
    return True


def peers_changed(account: str, space: str, plugin: str, *, ended: str = "") -> None:
    """Who is live with this PC moved: bars and panels hear it (``ended``: why Live ended by
    itself, :func:`leave`)."""
    if room_of(plugin, account, space) is None and not is_live(plugin):
        _push({"type": "live_peers", "plugin": plugin, "on": False, "peers": [], **({"ended": ended} if ended else {})})
        return
    st = status(plugin, come_along=False)
    _push({"type": "live_peers", "plugin": plugin, "on": st["on"], "state": st["state"], "peers": st["peers"]})
    if not ended:
        _team_changed(account, space)  # who the menu reaches moved


def forget(plugin: str, *, keep_live: bool = False) -> None:
    """The plugin unloaded: its callbacks and holds go, and Live ends for it, unless it is
    coming straight back (``keep_live``: a reload, an update). Then its group never sees it
    leave: a teammate left alone in the group would end their Live."""
    if not keep_live:
        leave(plugin)
    with _lock:
        _listeners.pop(plugin, None)
        _holds.pop(plugin, None)


def reset_for_tests() -> None:
    with _lock:
        _live.clear()
        _holds.clear()
        _told_holds.clear()
        _sent.clear()
        _listeners.clear()
        _kept.clear()
        _join_reload.clear()
        _moving.clear()
        _company.clear()
        _teams.clear()
        _own.clear()
        _sent_shas.clear()
        _relayed.clear()
        _led_by.clear()
        _my_states.clear()
        _their_states.clear()
        _shared.clear()
        _shared_sent.clear()
        _marks.clear()
        _beating.clear()
        _legacy.clear()
        _noted.clear()
        _invites.clear()
        _told_invites.clear()


class LiveApi:
    """``api.live`` for plugin backends."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def join(self, room: str = "") -> dict[str, Any]:
        return join(self.plugin_id, room)

    def leave(self) -> dict[str, Any]:
        return leave_live(self.plugin_id)

    def send(self, topic: str, data: Any, to: str = "") -> dict[str, Any]:
        return send(self.plugin_id, topic, data, to=to)

    def on(self, topic: str, callback: Callable[[dict[str, str], Any], Any]) -> None:
        """``callback(sender, data)`` for each ``topic`` message from a teammate."""
        if not callable(callback):
            raise TypeError("live.on needs a function: callback(sender, data)")
        with _lock:
            _listeners.setdefault(self.plugin_id, {}).setdefault(str(topic), []).append(callback)

    def state(self) -> dict[str, Any]:
        return status(self.plugin_id)

    def set_my_state(self, state: dict[str, Any] | None) -> dict[str, Any]:
        """This PC's live state (a camera, a tool): every groupmate sees it (``member_states``)."""
        return set_my_state(self.plugin_id, state)

    def member_states(self) -> list[dict[str, Any]]:
        """Every groupmate's live state: ``[{from, state}]``."""
        return member_states(self.plugin_id)

    def share(self, key: str, value: Any) -> dict[str, Any]:
        """The group's value of ``key`` (a component's state: playing, the picked clip; None
        drops it), through its leader: every groupmate ends on the same one."""
        return share(self.plugin_id, key, value)

    def shared(self) -> dict[str, dict[str, Any]]:
        """The group's values now: ``{key: {value, by}}``."""
        return shared(self.plugin_id)

    def on_shared(self, callback: Callable[[str, Any, dict[str, Any]], Any]) -> None:
        """``callback(key, value, by)`` when one of the group's values changes."""
        if not callable(callback):
            raise TypeError("live.on_shared needs a function: callback(key, value, by)")
        with _lock:
            _listeners.setdefault(self.plugin_id, {}).setdefault(SHARE, []).append(callback)

    def on_member_state(self, callback: Callable[[dict[str, str], Any], Any]) -> None:
        """``callback(sender, state)`` when a groupmate's live state changes (None: gone)."""
        if not callable(callback):
            raise TypeError("live.on_member_state needs a function: callback(sender, state)")
        with _lock:
            _listeners.setdefault(self.plugin_id, {}).setdefault(STATE, []).append(callback)
