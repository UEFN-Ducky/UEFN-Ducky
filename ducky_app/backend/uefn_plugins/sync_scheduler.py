"""Background rounds for shared spaces, in the app process only.

Before, a shared space synced only while a plugin's scope bar was on screen (a round a
minute) or the Workflows view was open: an agent's edits made with the panel closed
never went out, and this PC never pulled. This daemon keeps every space in step:

- a write to a shared copy starts a round 5 s after the last write, at most one round
  per space every 30 s (1 s and 3 s while a plugin is live there: its group pulls each
  change as soon as it's committed). Writes in any process count: the MCP bridge writes
  the same database, and ``PRAGMA data_version``, read once a second, says when anyone did;
- a round every 60 s while a linked plugin's panel is visible or the Workflows view is
  open, every 15 minutes otherwise;
- one round per linked space at start, and again when the account or the provider
  changes;
- changes queued again without a new write (Upload my copy, Retry) keep a round coming
  every 30 s while each one still sends some of them;
- after a round that failed (``error``, ``offline``, ``unavailable``) the space is tried
  again after 1 and 5 minutes, then every 15;
- at exit, :func:`flush` sends what is still queued, for at most 5 s, and releases the
  auto locks this PC holds.

Plugins whose provider hands core a ledger (``Provider.ledger``, a team copy) go through
core's version engine (:mod:`vcs_engine`); the provider's own round keeps the rest. A
round of a team space asks ``heads`` first, then pulls the plugins with changesets
waiting and pushes the ones with closed write groups. A team space is active while a
linked panel or the Workflows view is on screen, it changed here in the last 5 minutes,
this PC holds a lock there or has changes to send: then its round comes every 60 s and
its presence goes out when it changed (and each minute to stay listed), with the locks it
holds renewed; idle, every 15 minutes and no presence. A provider's channel event
(``spaces.remote_event``) starts a round within seconds. A writer waiting for a lock (a
``required`` prefix) is answered within a tick or two.

Plugins that keep declared folders (``team_folders``) in a team space: their copies are
looked at (:func:`vcs_engine.watch`) every 2 s while the plugin's panel is on screen or
one of its calls ran in the last 2 minutes, right after each call returns, and every
minute otherwise; a unit quiet for 3 s, files to fetch again, or changes that waited
behind Update now (the panel was hidden) start a round. A pull that changed a plugin's
files runs its ``after_pull``, and a plugin loaded here whose folders moved (a pull, a
switch, a project change, in any process) restarts once none of its calls is running.
Once per account, plugins linked to a team before their folders could be shared go back
on Local with the bar's offer (:func:`vcs_engine.migrate_folder_links`).

Every round is wrapped in ``spaces._round_failed``: one that raises is recorded on the
space and the next tick runs. Rounds of one space never overlap, in any process: each
holds the space's round lock (:func:`run_round`). Never runs in the MCP bridge
(``UEFN_DUCKY_MCP_BRIDGE=1``): the app's scheduler sees the bridge's writes.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_log = logging.getLogger(__name__)

TICK_S = 1.0
DEBOUNCE_S = 5.0  # a round this long after the last write to a space
MIN_GAP_S = 30.0  # at most one round per space this often
LIVE_DEBOUNCE_S = 1.0  # the same while a plugin is live in the space
LIVE_GAP_S = 3.0
ACTIVE_EVERY_S = 60.0  # while a linked plugin's panel or the Workflows view is on screen
IDLE_EVERY_S = 15 * 60.0
RETRY_AFTER_S = (60.0, 5 * 60.0, 15 * 60.0)  # after 1, 2, 3 or more failed rounds in a row
REFRESH_EVERY_S = 30.0  # links, panels and sync rows are read again at least this often,
REFRESH_MIN_S = 5.0  # and after someone else's write at most this often
FLUSH_TIMEOUT_S = 5.0
ROUND_WAIT_S = 10 * 60.0  # how long a forced round waits for a round of the same space to end
RETRY_STATES = frozenset({"error", "offline", "unavailable"})
# Access gone, or the server's copy was reset: writes there are refused, and a round
# every 15 minutes is enough to see it come back.
SLOW_STATES = frozenset({"lost", "reset"})
# Workflow docs' plugin id (owned.DOC_PLUGIN). The Workflows view reports itself as this
# plugin's panel, so panel_presence says when it is on screen.
WORKFLOWS = "ducky.automations"
# Team spaces (plugins on a ledger).
ACTIVE_FOR_S = 5 * 60.0  # a change made here keeps the space active this long
WAKE_GAP_S = 5.0  # a provider's events start a round at most this often per space
WAKE_KEEP_S = 120.0  # an event for a space not known yet waits this long for it
SIDE_EVERY_S = 5.0  # an active space's presence and locks are looked at this often
PRUNE_EVERY_S = 60.0
GROUP_OPEN_MAX_S = 3600.0  # a write group open this long was left by a call that never returned
# The provider's own round keeps these: access gone or unsure, or the data reset on the
# server (nothing goes out until the person chooses).
HOLD_STATES = frozenset({"lost", "unavailable", "reset"})
# Folders plugins keep themselves (team_folders), in a team space.
FOLDER_FAST_S = 2.0  # a look this often while the panel is on screen or a call ran lately
FOLDER_SLOW_S = 60.0  # otherwise
FOLDER_IN_USE_S = 120.0  # a call this recent keeps the plugin in use


def _in_bridge() -> bool:
    """Not the window: a tool server or an agent host (rounds run only in the app)."""
    from backend.process_role import role

    return role() != "app"


def _round_lock(account: str, space: str, wait: float) -> contextlib.AbstractContextManager[bool]:
    """The space's round lock: an OS file lock, so a round in the MCP bridge (a plugin
    tool asking for one) and one here never overlap, and a process that dies frees it.
    Yields False when another round still holds it after ``wait`` seconds. Before, the
    provider's lock was per process: two rounds pushed at once, and the one whose push
    went stale replaced an edit the other hadn't confirmed yet. A thread that already
    holds it gets it again at once (a round's own steps take it too)."""
    from backend.util.file_lock import held_file_lock

    return held_file_lock(f"space-round-{account}-{space}", wait)


def run_round(account: str, space: str, *, force: bool, pull: Any = (), push_only: bool = False) -> dict[str, Any]:
    """One round for ``(account, space)``, holding the space's round lock: a forced round
    waits for one already running (up to :data:`ROUND_WAIT_S`), any other answers ``busy``.
    Plugins with a ledger go through the version engine (``pull``: plugins to pull even
    when heads says nothing moved; ``push_only``: send what is queued, no heads or pulls),
    the provider's own round keeps the rest. A round that raises is recorded on the space
    (state ``error`` and the provider plugin's errors) instead of escaping."""
    from backend.uefn_plugins import spaces

    try:
        with _round_lock(account, space, ROUND_WAIT_S if force else 0.0) as held:
            if not held:
                return {"state": "busy", "changed": [], "error": ""}
            return _space_round(account, space, force=force, pull=frozenset(pull or ()), push_only=push_only)
    except Exception as exc:  # noqa: BLE001 — any failure is the space's, never the caller's
        return spaces._round_failed({"account": account, "id": space}, exc)


def space_plugins(account: str, space: str) -> set[str]:
    """Plugins whose copy lives in the space for this account: the ones linked to it, its
    workflows when this account keeps workflows there, and any with rows there."""
    from backend.store.repos import plugin_data as repo
    from backend.store.switch import use_db
    from backend.uefn_plugins import scopes

    out = {p for p, s in repo.links(account).items() if s == space and scopes.sharing_allowed(p)}
    out.update(repo.scope_plugin_ids(account, space))
    if use_db("automations"):
        from backend.store.repos import workflows as runtime

        if space in runtime.synced_spaces(account):
            out.add(WORKFLOWS)
    return out


def _space_round(account: str, space: str, *, force: bool, pull: frozenset[str], push_only: bool) -> dict[str, Any]:
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import spaces

    members = space_plugins(account, space)
    team = spaces.ledgers(account, space, members) if members else {}
    if not team or repo.sync_get(account, space)["state"] in HOLD_STATES:
        return spaces.provider().sync(account, space, force=force) or {}
    out = _ledger_round(account, space, team, force=force, pull=pull, push_only=push_only)
    if members - set(team):  # plugins still on the provider's own sync
        older = spaces.provider().sync(account, space, force=force) or {}
        out = {
            "state": out["state"] if out["state"] != "ok" else str(older.get("state") or "ok"),
            "changed": [*out["changed"], *[p for p in older.get("changed") or [] if p not in out["changed"]]],
            "keys": out["keys"],
            "error": out["error"] or str(older.get("error") or ""),
        }
    return out


def _ledger_work(account: str, space: str, plugin: str, now: float) -> bool:
    """Something to do for ``plugin``: its first round on the new sync (the move from the
    older one), changesets waiting on the server, changes to send, a set-aside download due
    again, folder units changed here (:func:`vcs_engine.folder_work`)."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import vcs_engine

    head = space_vcs.head_get(account, space, plugin)
    return (head["engine"] != "v2" or int(head["remote_seq"]) > int(head["seq"])
            or space_vcs.has_outgoing(account, space, plugin, now) or bool(space_vcs.cs_open(account, space, plugin))
            or bool(space_vcs.blocked(account, space, plugin, due=now))
            or vcs_engine.folder_work(account, space, plugin))


def _ledger_round(account: str, space: str, team: dict[str, Any], *, force: bool, pull: frozenset[str],
                  push_only: bool) -> dict[str, Any]:
    """Heads (what moved, locks, the roster), then a version-engine round for each plugin
    with something to do (all of them when ``force``). A plugin's first round reconciles
    its copy and puts it on the new sync (its writers group, lock and note from then on).
    ``keys``: per changed plugin, the doc keys and file paths its pulls changed."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import vcs_engine

    now = time.time()
    importing: dict[str, Any] = {}
    if not push_only:
        seen = vcs_engine.refresh(account, space, sorted(team), next(iter(team.values())), now=now)
        if seen["state"] != "ok":
            error = str(seen.get("error") or "")
            if seen["state"] in ("offline", "error"):  # the bar says "Not synced" and why
                repo.sync_put(account, space, called_at=now, state=seen["state"], error=error)
            else:
                repo.sync_put(account, space, called_at=now)
            return {"state": seen["state"], "changed": [], "keys": {}, "error": error}
        importing = dict(seen.get("importing") or {})
    changed: list[str] = []
    keys: dict[str, list[str]] = {}
    failed: list[tuple[str, str]] = []
    for plugin, backend in sorted(team.items(), key=lambda kv: (kv[0] not in pull, kv[0])):  # asked-for pulls first
        if plugin in importing:
            continue  # it stays on the older copy until its move to the new sync is done
        if not (force or plugin in pull or _ledger_work(account, space, plugin, now)):
            continue
        got = vcs_engine.run(account, space, plugin, backend, force=force, pull=False if push_only else None, now=now)
        if got.get("skipped") or got.get("state") == "busy":
            continue
        if got.get("tree"):  # its folder files changed: reload it, or tell its panels
            vcs_engine.after_pull(plugin)
        changed += [p for p in got.get("changed") or [] if p not in changed]
        if got.get("keys"):
            keys[plugin] = list(got["keys"])
        if got.get("state") != "ok":
            failed.append((str(got.get("state")), str(got.get("error") or "")))
    fields: dict[str, Any] = {"called_at": now}
    if not failed:
        fields["synced_at"] = now
        if repo.sync_get(account, space)["state"] in ("offline", "error"):
            fields.update(state="ok", error="")
    repo.sync_put(account, space, **fields)
    state, error = failed[0] if failed else ("ok", "")
    return {"state": state, "changed": changed, "keys": {p: k for p, k in keys.items() if p in changed}, "error": error}


def synced_event(out: dict[str, Any]) -> dict[str, Any]:
    """The push after a round: ``plugins`` whose data it changed (their bars refresh and
    their panels merge it), ``keys`` per plugin when known (the doc keys and file paths that
    changed), ``synced`` (every bar refreshes its status)."""
    event: dict[str, Any] = {"type": "plugin_scope_changed", "plugins": [str(p) for p in out.get("changed") or []],
                             "synced": True}
    keys = out.get("keys")
    if isinstance(keys, dict) and keys:
        event["keys"] = {str(p): [str(k) for k in v] for p, v in keys.items() if p in event["plugins"]}
    return event


def _announce(out: dict[str, Any]) -> None:
    """Open panels and scope bars refresh after a round, like a round the bar started."""
    changed = [str(p) for p in out.get("changed") or []]
    if not changed and (out.get("skipped") or out.get("state") == "busy"):
        return
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event(synced_event(out))
        if WORKFLOWS in changed:
            push_ui_event({"type": "graphs_changed"})
    except Exception:  # noqa: BLE001 — the next status read shows it anyway
        _log.debug("announcing a sync round failed", exc_info=True)


def _visible(plugin: str) -> bool:
    from backend.uefn_plugins.panel_presence import panel_state

    return bool(panel_state(plugin)["visible"])


def _plan(account: str) -> dict[str, tuple[set[str], bool, float]]:
    """``{space: (what lives there, on screen, newest unsent write)}``: spaces linked
    plugins keep their data in, spaces this account keeps workflows in (every one while
    the Workflows view is open), and any space with changes still to send."""
    from backend.store.repos import plugin_data as repo
    from backend.store.switch import use_db
    from backend.uefn_plugins import scopes

    held: dict[str, set[str]] = {}
    for plugin, space in repo.links(account).items():
        if scopes.valid_space_id(space) and scopes.sharing_allowed(plugin):
            held.setdefault(space, set()).add(plugin)
    view = False
    if use_db("automations"):
        from backend.store.repos import workflows as runtime

        view = _visible(WORKFLOWS)
        for space in runtime.synced_spaces(account):
            if scopes.valid_space_id(space) and (view or repo.has_rows(account, WORKFLOWS, space)):
                held.setdefault(space, set()).add(WORKFLOWS)
    unsent = {
        space: at for space, at in repo.unsent_spaces(account).items()
        if scopes.valid_space_id(space) and not space.startswith(scopes.SANDBOX_PREFIX)
    }
    out: dict[str, tuple[set[str], bool, float]] = {}
    for space in set(held) | set(unsent):
        plugins = held.get(space, set())
        active = any(view if plugin == WORKFLOWS else _visible(plugin) for plugin in plugins)
        out[space] = (plugins, active, unsent.get(space, 0.0))
    return out


@dataclass
class _Space:
    plugins: set[str] = field(default_factory=set)  # linked plugin ids, WORKFLOWS for workflows
    active: bool = False  # one of them is on screen
    state: str = "ok"  # the sync row's state (the provider's last word)
    called_at: float = 0.0  # when the provider last started a round for it, in any process
    written: float = 0.0  # newest change still to send (0: none)
    started: float = 0.0  # when this scheduler last started a round for it
    sent: float = 0.0  # start of its last round the provider ran (changes before it went with it)
    fails: int = 0  # failed rounds in a row
    owed: bool = False  # the round at start is still to run
    left: int = 0  # changes still to send at the last look
    looked: float = 0.0  # called_at at the last look
    more: bool = False  # rounds since the last look sent some of them, and some are left
    running: bool = False
    team: frozenset[str] = frozenset()  # plugins on a ledger: the version engine syncs them
    hot: bool = False  # a team space in active use (see the module docstring)
    wrote: float = 0.0  # newest change made here this scheduler saw (activity, changes to send)
    wake: bool = False  # a provider's event asks for a round
    pull: set[str] = field(default_factory=set)  # plugins an event says have new changesets
    side_at: float = float("-inf")  # last look at its presence and locks
    side_running: bool = False
    live: bool = False  # a plugin is live there (live.spaces_live)
    holding: bool = False  # this PC holds a control there (live.spaces_holding)


_WAKES: dict[tuple[str, str], tuple[float, frozenset[str]]] = {}  # (account, space) → (when, plugins to pull)
_WAKE_LOCK = threading.Lock()


def wake(account: str, space: str, plugin: str | None = None) -> None:
    """A provider heard about the space (``spaces.remote_event``): the scheduler starts a
    round for it on its next tick (heads; ``plugin`` is pulled), at most one per
    :data:`WAKE_GAP_S`."""
    with _WAKE_LOCK:
        _, plugins = _WAKES.get((account, space), (0.0, frozenset()))
        _WAKES[(account, space)] = (time.time(), plugins | ({plugin} if plugin else frozenset()))


def _team_looks(account: str, found: dict[str, tuple[set[str], bool, float]]) -> dict[str, tuple[frozenset[str], float, bool, float]]:
    """``{space: (plugins on a ledger, newest change made here, a lock held here, newest
    closed write group with changes to send)}`` of the spaces that have any."""
    from backend.store.repos import plugin_data as repo
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import spaces

    out: dict[str, tuple[frozenset[str], float, bool, float]] = {}
    closed: dict[str, float] | None = None
    for space, (plugins, _, _) in found.items():
        team = frozenset(spaces.ledgers(account, space, plugins | set(repo.scope_plugin_ids(account, space))))
        if not team:
            continue
        if closed is None:
            closed = space_vcs.groups_closed(account)
        holds = any(lk["mine"] for lk in space_vcs.locks(account, space))
        out[space] = (team, space_vcs.last_activity(account, space), holds, closed.get(space, 0.0))
    return out


class Scheduler:
    """The cadence above. :meth:`tick` is one look (the daemon calls it every second);
    tests drive it with their own clock, and ``spawn`` runs a round (a thread here)."""

    def __init__(self, *, clock: Callable[[], float] = time.time,
                 spawn: Callable[[Callable[[], None]], None] | None = None) -> None:
        self.clock = clock
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, name="space-sync-round", daemon=True).start())
        self.spaces: dict[str, _Space] = {}
        self._lock = threading.Lock()
        self._who = ("", "")  # (account, provider plugin) the spaces belong to
        self._version: int | None = None
        self._refreshed = float("-inf")
        self._stale = True
        self._pruned = float("-inf")
        self._chores_running = False
        # Folder plugins in team spaces: (space, plugin) → (last look, its last call seen then).
        self._folder_looks: dict[tuple[str, str], tuple[float, float]] = {}
        self._folder_running: set[str] = set()  # spaces with a look running
        self._migrated: set[str] = set()  # accounts whose folder links were looked at once

    def tick(self, now: float | None = None) -> list[str]:
        """Start every round that is due, and a team space's presence and lock look when
        due. Returns the spaces whose round started."""
        from backend.store import db
        from backend.uefn_plugins import live, spaces

        now = self.clock() if now is None else now
        version = db.data_version(db.connect())
        if version != self._version:
            self._version, self._stale = version, True
        provider = spaces.provider_plugin() if spaces.has_provider() else ""
        since = now - self._refreshed
        live_now = live.spaces_live()
        holding = live.spaces_holding()
        # Live: a write is seen within a tick, so the group gets it in a few seconds.
        soonest = TICK_S if live_now else REFRESH_MIN_S
        if provider != self._who[1] or since >= REFRESH_EVERY_S or (self._stale and since >= soonest):
            self._refresh(now, provider)
        self._take_wakes()
        due: list[tuple[str, bool, frozenset[str]]] = []
        side: list[tuple[str, list[str]]] = []
        with self._lock:
            account = self._who[0]
            for space, rec in self.spaces.items():
                rec.live = (account, space) in live_now
                rec.holding = (account, space) in holding
                force = self._due(rec, now)
                if force is not None:
                    rec.running, rec.owed, rec.started = True, False, now
                    due.append((space, force, frozenset(rec.pull)))
                    rec.pull, rec.wake = set(), False
                # Live counts as in use: a live PC whose panel is hidden still beats, or its
                # presence runs out and the group drops its links. So does a held control:
                # presence is how a teammate whose page opens later greys it out.
                if rec.team and (rec.hot or rec.live or rec.holding) and not rec.side_running and now >= rec.side_at + SIDE_EVERY_S:
                    rec.side_running, rec.side_at = True, now
                    side.append((space, sorted(rec.team)))
            teams = any(rec.team for rec in self.spaces.values())
        for space, force, pulls in due:
            self.spawn(lambda space=space, force=force, pulls=pulls: self._round(account, space, force, now, pulls))
        for space, plugins in side:
            self.spawn(lambda space=space, plugins=plugins: self._side(account, space, plugins))
        if teams:
            for space, plugins in self._folders_due(now):
                self.spawn(lambda space=space, plugins=plugins: self._folder_look(account, space, plugins))
        self._chores(account, now, teams)
        _reload_moved()
        return [space for space, _, _ in due]

    def _folders_due(self, now: float) -> list[tuple[str, list[str]]]:
        """Team spaces whose folder plugins are due a look: every :data:`FOLDER_FAST_S` while
        a plugin's panel is on screen or one of its calls runs or ran in the last
        :data:`FOLDER_IN_USE_S`, right after a call returned, else every :data:`FOLDER_SLOW_S`."""
        from backend.store.repos import folder_files
        from backend.uefn_plugins import team_folders

        with self._lock:
            looks = [(space, sorted(rec.team)) for space, rec in self.spaces.items()
                     if rec.team and space not in self._folder_running]
        out: list[tuple[str, list[str], dict[str, float]]] = []
        for space, team in looks:
            due: dict[str, float] = {}
            for plugin in team:
                if not team_folders.plugin_folders(plugin):
                    continue
                last, seen = self._folder_looks.get((space, plugin), (float("-inf"), 0.0))
                called = float(folder_files.last_call(plugin).get("at") or 0.0)
                busy = now - called < FOLDER_IN_USE_S or bool(folder_files.calls(plugin)) or _visible(plugin)
                if called > seen or now >= last + (FOLDER_FAST_S if busy else FOLDER_SLOW_S):
                    due[plugin] = called
            if due:
                out.append((space, sorted(due), due))
        picked: list[tuple[str, list[str]]] = []
        with self._lock:
            for space, plugins, calls in out:
                if space in self._folder_running:
                    continue
                self._folder_running.add(space)
                for plugin in plugins:
                    self._folder_looks[(space, plugin)] = (now, calls[plugin])
                picked.append((space, plugins))
        return picked

    def _folder_look(self, account: str, space: str, plugins: list[str]) -> None:
        """Look at the plugins' folder copies (:func:`vcs_engine.watch`): what changed is
        noted and locked; a quiet unit, files to fetch again, or changes that may land now
        start a round for the plugin."""
        from backend.uefn_plugins import spaces, vcs_engine

        try:
            for plugin, backend in spaces.ledgers(account, space, plugins).items():
                seen = vcs_engine.watch(account, space, plugin, backend)
                if seen.get("quiet") or vcs_engine.folder_due(account, space, plugin):
                    wake(account, space, plugin)
        except Exception:  # noqa: BLE001 — the next look tries again
            _log.debug("looking at the folders in %s failed", space, exc_info=True)
        finally:
            with self._lock:
                self._folder_running.discard(space)

    def _take_wakes(self) -> None:
        """Provider events into the spaces they are about (one for a space not known yet
        waits for the next refresh, :data:`WAKE_KEEP_S` at most)."""
        with _WAKE_LOCK:
            pending = dict(_WAKES)
        if not pending:
            return
        now = time.time()
        done: list[tuple[str, str]] = []
        with self._lock:
            for (account, space), (at, plugins) in pending.items():
                rec = self.spaces.get(space) if account == self._who[0] else None
                if rec is not None:
                    rec.wake = True
                    rec.pull |= set(plugins)
                    done.append((account, space))
                elif now - at > WAKE_KEEP_S:
                    done.append((account, space))
                else:
                    self._stale = True
        with _WAKE_LOCK:
            for key in done:
                if _WAKES.get(key) is pending[key]:  # a newer event stays for the next tick
                    del _WAKES[key]

    def _refresh(self, now: float, provider: str) -> None:
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins import scopes

        self._refreshed, self._stale = now, False
        account = scopes.account_id() if provider else scopes.LOCAL
        found = {} if account == scopes.LOCAL else _plan(account)
        rows = {space: repo.sync_get(account, space) for space in found}
        left = {space: repo.count_dirty(account, space) for space, (_, _, written) in found.items() if written}
        teams = _team_looks(account, found) if found else {}
        if account != scopes.LOCAL and account not in self._migrated:
            self._migrated.add(account)
            self.spawn(_migrate_folder_links)
        with self._lock:
            fresh = (account, provider) != self._who
            if fresh:
                self._who, self.spaces = (account, provider), {}
            for space, (plugins, active, written) in found.items():
                rec = self.spaces.get(space)
                if rec is None:
                    rec = self.spaces[space] = _Space(owed=fresh and bool(plugins))
                team, wrote, holds, closed = teams.get(space, (frozenset(), 0.0, False, 0.0))
                if team:
                    # A write group's changes go out a few seconds after its call returned.
                    written = max(written, closed)
                    rec.wrote = max(rec.wrote, wrote, written)
                rec.plugins, rec.active, rec.written, rec.team = plugins, active, written, team
                rec.hot = bool(team) and (active or bool(written) or holds or now - rec.wrote < ACTIVE_FOR_S)
                rec.state, rec.called_at = str(rows[space]["state"] or "ok"), float(rows[space]["called_at"] or 0.0)
                if rec.state not in RETRY_STATES and rec.called_at > rec.started:
                    rec.fails = 0  # a round started elsewhere (the scope bar, a Save) worked since
                # Changes queued again carry no new write (Upload my copy, Retry), and the
                # provider sends a batch per round: while the rounds since the last look,
                # started anywhere, still sent some, the next one comes as soon as allowed.
                # One that sent nothing (rows it keeps skipping) ends it, so it can't loop.
                # A backlog seen for the first time gets one more round to find out.
                count = left.get(space, 0)
                if not count:
                    rec.more = False
                elif rec.called_at > rec.looked:
                    rec.more = not rec.left or count < rec.left
                rec.left, rec.looked = count, rec.called_at
            for space in [s for s, rec in self.spaces.items() if s not in found and not rec.running]:
                del self.spaces[space]

    @staticmethod
    def _due(rec: _Space, now: float) -> bool | None:
        """None: not yet. Otherwise the round's ``force``: True for changes to send (it
        must beat the provider's own minute limit), False for a pull it may skip."""
        if rec.running:
            return None
        unsent = rec.written > max(rec.sent, rec.called_at) or rec.more
        if rec.owed:
            return unsent
        last = max(rec.started, rec.called_at)
        gap = LIVE_GAP_S if rec.live else MIN_GAP_S
        if rec.wake and now >= last + min(gap, WAKE_GAP_S):  # a teammate's change, a lock, presence
            return unsent
        failing = rec.fails > 0 or rec.state in RETRY_STATES
        wait = RETRY_AFTER_S[min(max(rec.fails, 1), len(RETRY_AFTER_S)) - 1] if failing else 0.0
        slow = rec.state in SLOW_STATES
        debounce = LIVE_DEBOUNCE_S if rec.live else DEBOUNCE_S
        if unsent and not slow and now >= max(rec.written + debounce, last + gap, last + wait):
            return True
        every = IDLE_EVERY_S if slow else wait or (ACTIVE_EVERY_S if rec.active or rec.hot or rec.live else IDLE_EVERY_S)
        return False if now >= last + every else None

    def _side(self, account: str, space: str, plugins: list[str]) -> None:
        """An active team space: presence when it changed (or each minute), locks of mine
        renewed each minute, auto locks released 30 s after their last change went out
        or once the plugin's tab closed."""
        from backend.uefn_plugins import spaces, vcs_engine

        try:
            team = spaces.ledgers(account, space, plugins)
            if team:
                vcs_engine.beat(account, space, next(iter(team.values())), list(team))
                for plugin, backend in team.items():
                    due = vcs_engine.renew_due(account, space, plugin, active=True)
                    if due:
                        vcs_engine.renew(account, space, plugin, backend, due)
                    vcs_engine.release_idle(account, space, plugin, backend)
        except Exception:  # noqa: BLE001 — the next look tries again
            _log.debug("presence and locks of %s failed", space, exc_info=True)
        finally:
            with self._lock:
                rec = self.spaces.get(space)
                if rec is not None:
                    rec.side_running = False

    def _chores(self, account: str, now: float, teams: bool) -> None:
        """Writers waiting for a lock (each tick, while any space has a ledger), and every
        minute the housekeeping: old activity, changesets and answered requests go, and
        write groups left open by a call that never returned close."""
        from backend.uefn_plugins import scopes

        if not account or account == scopes.LOCAL or self._chores_running:
            return
        from backend.store.repos import space_vcs

        prune = now >= self._pruned + PRUNE_EVERY_S
        asks = teams and bool(space_vcs.lock_requests_pending(account))
        if not (prune or asks):
            return
        if prune:
            self._pruned = now
        self._chores_running = True

        def work() -> None:
            from backend.uefn_plugins import spaces, vcs_engine

            try:
                if asks and vcs_engine.serve_lock_requests(
                        account, lambda space, plugin: spaces.ledger_for(account, space, plugin)):
                    self._stale = True  # a lock held now makes its space active
                if prune:
                    space_vcs.prune()
                    space_vcs.groups_close_stale(GROUP_OPEN_MAX_S)
            except Exception:  # noqa: BLE001 — the next tick tries again
                _log.debug("space sync chores failed", exc_info=True)
            finally:
                self._chores_running = False

        self.spawn(work)

    def _round(self, account: str, space: str, force: bool, started: float, pull: frozenset[str] = frozenset()) -> None:
        from backend.store.repos import plugin_data as repo

        out = run_round(account, space, force=force, pull=pull)
        state = str(out.get("state") or "")
        ran = state != "busy" and not out.get("skipped")  # busy: another round holds the space
        try:
            called_at = float(repo.sync_get(account, space)["called_at"] or 0.0)
        except Exception:  # noqa: BLE001 — the next refresh reads it
            called_at = 0.0
        with self._lock:
            rec = self.spaces.get(space) if self._who[0] == account else None
            if rec is not None:
                rec.running = False
                rec.called_at = max(rec.called_at, called_at)
                if state == "busy":  # another round held the space: this one's wake and pulls wait for the next tick
                    rec.wake = True
                    rec.pull |= set(pull)
                if ran:
                    rec.sent = started
                    rec.fails = rec.fails + 1 if state in RETRY_STATES else 0
                    rec.state = state or rec.state
            self._stale = True
        _announce(out)


def _reload_moved() -> None:
    """Plugins loaded here whose folders moved since they loaded restart
    (:func:`host.reload_moved_plugins`). Nothing is loaded before the plugin host is."""
    import sys

    host = sys.modules.get("backend.uefn_plugins.host")
    if host is None:
        return
    try:
        host.reload_moved_plugins()
    except Exception:  # noqa: BLE001 — the next tick looks again
        _log.debug("restarting plugins whose folders moved failed", exc_info=True)


def _migrate_folder_links() -> None:
    """Once per account: plugins linked to a team before their folders could be shared go
    back on Local, with the bar's offer to copy Local into the team; they restart on it."""
    from backend.uefn_plugins import vcs_engine

    try:
        moved = vcs_engine.migrate_folder_links()
        if not moved:
            return
        from backend.uefn_plugins.host import is_plugin_enabled, reload_single_plugin

        for plugin in moved:
            if is_plugin_enabled(plugin):
                reload_single_plugin(plugin)
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "plugin_scope_changed", "plugins": moved, "switched": True})
    except Exception:  # noqa: BLE001 — each plugin's bind before load does it too
        _log.debug("moving folder plugins back to Local failed", exc_info=True)


def flush(timeout: float = FLUSH_TIMEOUT_S) -> int:
    """At exit (``shutdown.hard_exit``): one round for every space with changes still to
    send (team copies only send, they don't pull), all at once, and the auto locks this PC
    holds released, for at most ``timeout`` seconds in all; what still runs then is left
    behind. Returns how many rounds started. Never raises."""
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins import scopes, spaces

        if _in_bridge() or not spaces.has_provider():
            return 0
        account = scopes.account_id()
        if account == scopes.LOCAL:
            return 0
        unsent = repo.unsent_spaces(account)
        targets = [
            space for space in sorted(unsent)
            if scopes.valid_space_id(space) and not space.startswith(scopes.SANDBOX_PREFIX)
            and repo.sync_get(account, space)["state"] not in SLOW_STATES
        ]
        linked = sorted({s for s in repo.links(account).values() if scopes.valid_space_id(s)} | set(targets))
    except Exception:  # noqa: BLE001 — exit goes on
        _log.debug("sync flush skipped", exc_info=True)
        return 0
    workers = [
        threading.Thread(target=run_round, args=(account, space), kwargs={"force": True, "push_only": True},
                         name="space-sync-flush", daemon=True)
        for space in targets
    ]

    def release() -> None:
        for space in linked:
            try:
                spaces.release_auto_locks(account, space)
            except Exception:  # noqa: BLE001 — their leases run out on the server
                _log.debug("releasing locks of %s at exit failed", space, exc_info=True)

    releaser = threading.Thread(target=release, name="space-lock-release", daemon=True)
    for worker in [*workers, releaser]:
        worker.start()
    for worker in [*workers, releaser]:
        worker.join(max(0.0, deadline - time.monotonic()))
    return len(workers)


def space_active(account: str, space: str) -> bool:
    """``spaces.space_active``: the running scheduler's word for a space it follows,
    else a look of its own (a panel on screen, a change in the last few minutes, a lock
    held here, changes to send)."""
    sched = _SCHEDULER
    if sched is not None:
        with sched._lock:
            rec = sched.spaces.get(space) if sched._who[0] == account else None
            if rec is not None:
                return rec.active or rec.hot
    from backend.store.repos import plugin_data as repo
    from backend.store.repos import space_vcs

    if repo.count_dirty(account, space) or time.time() - space_vcs.last_activity(account, space) < ACTIVE_FOR_S:
        return True
    if any(lk["mine"] for lk in space_vcs.locks(account, space)):
        return True
    return any(_visible(plugin) for plugin in space_plugins(account, space))


_START_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_SCHEDULER: Scheduler | None = None  # the daemon's, in this process


def start() -> bool:
    """Start the daemon (app process only). False in the MCP bridge, or when it runs."""
    global _THREAD, _SCHEDULER
    if _in_bridge():
        return False
    with _START_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _STOP.clear()
        _SCHEDULER = Scheduler()
        _THREAD = threading.Thread(target=_loop, args=(_SCHEDULER,), name="space-sync-scheduler", daemon=True)
        _THREAD.start()
    return True


def stop() -> None:
    _STOP.set()


def _loop(scheduler: Scheduler) -> None:
    while not _STOP.wait(TICK_S):
        try:
            scheduler.tick()
        except Exception:  # noqa: BLE001 — a bad look never stops the next one
            _log.debug("space sync tick failed", exc_info=True)
