"""Team sync, through the spaces provider: the one door core uses to the team-sync engine.

The engine (versioned sync, team folders, live editing, the background scheduler and their
tables' code) ships with the spaces provider plugin (Ducky Account). Core keeps only the DB
migrations, the ledger contract (:mod:`backend.uefn_plugins.ledger`) and this facade.

Callers write ``from backend.uefn_plugins.team_engine import vcs_engine`` and call
``vcs_engine.release(...)`` exactly as before. Each call asks the provider for its engine
(:meth:`backend.uefn_plugins.spaces.Provider.engine`) and runs the engine's function of the
same name. With no provider, no engine, or an engine without that name (an older plugin),
the call returns the default listed below and never raises. Errors the engine itself raises
pass through unchanged (callers already handle them).

Engine interface (what ``Provider.engine()`` returns)
-----------------------------------------------------
An object (a namespace or module) with six attributes, each a module-like object holding the
functions below by these exact names and signatures. A missing attribute or function
falls back to its default, so a plugin may implement a subset.

``vcs_engine``  (default with no engine → shown after the arrow)
  unit_label(account, space, plugin, unit) -> str                      → unit
  my_user_id() -> str                                                  → ""
  other_pc(account, space, user, device) -> str                        → ""
  lock_ducky(account, space, lk) -> str | None                         → None
  holder_of(account, space, lk) -> tuple[str, str]                     → ("", "")
  _held_items(c) -> list[list]                                         → []
  after_pull(plugin) -> None                                           → None
  apply_waiting(plugin, *, wait=...) -> dict                           → NO_ENGINE
  pull_copy(account, space, plugin, backend, *, label="", timeout=..., landed=None) -> (bool, str)
                                                                       → (False, NEEDS_ACCOUNT)
  send_copy(account, space, plugin, backend, *, label="", timeout=...) -> (bool, str)
                                                                       → (False, NEEDS_ACCOUNT)
  send_before_leaving(account, space, plugin, backend, *, timeout=...) -> list[str]   → []
  resolve_unit(account, space, plugin, backend, folder_id, unit, choice) -> dict      → NO_ENGINE
  folder_rows(account, space, plugin, backend=None) -> list[dict]      → []
  folder_outgoing(account, space, plugin) -> list[dict]                → []
  run(account, space, plugin, backend, *, push=None, pull=None, force=False, message="", ...) -> dict
                                                                       → NO_ENGINE
  set_mode(account, space, plugin, mode) -> dict                       → NO_ENGINE
  acquire(account, space, plugin, backend, units: [(unit, name)], *, reason, mode, now) -> {unit: dict}
                                                                       → {unit: NO_ENGINE} per unit
  release(account, space, plugin, backend, units) -> dict              → NO_ENGINE
  break_lock(account, space, plugin, backend, unit) -> dict            → NO_ENGINE
  ask(account, space, plugin, backend, unit) -> dict                   → NO_ENGINE
  device_names(account, space) -> {device: name}                       → {}
  resolve(account, space, plugin, kind, key, choice, backend=None) -> dict            → NO_ENGINE
  restore(account, space, plugin, backend, *, items=None, to_seq=None, keys=None, message="") -> dict
                                                                       → NO_ENGINE
  undo(account, space, plugin, backend, seq, *, message="") -> dict    → NO_ENGINE
  history(account, space, plugin, backend, *, key=None, before=None, limit=50) -> dict
                                                                       → NO_ENGINE + entries []
  save_unsent_to_local(account, space, *, label="") -> dict            → NO_ENGINE
  conflicts_into_rows(account, space) -> int                           → 0
  discard_unsent(account, space) -> dict                               → NO_ENGINE

``space_vcs``  (the engine's tables)
  row(conn, account, space, plugin, kind, key) -> dict | None          → None
  head_get(account, space, plugin) -> dict                             → HEAD_DEFAULT (engine "v1")
  cs_items_open(account, space, plugin) -> set[(kind, key)]            → set()
  conflict_get(account, space, plugin, kind, key, *, conn=None) -> dict | None        → None
  conflicts(account, space, plugin=None) -> list[dict]                 → []
  blocked(account, space, plugin=None, *, due=None) -> list[dict]      → []
  blocked_retry_now(account, space, plugin, kind, key) -> bool         → False
  locks(account, space, plugin=None, *, now=None, live=True) -> list[dict]            → []
  foreign_lock(account, space, plugin, unit, *, now=None, conn=None) -> dict | None   → None
  lock_delete(account, space, plugin, units) -> None                   → None
  presence_get(account, space) -> {roster, updated, sent_sha, sent_at} → empty roster
  activity_note(account, space, plugin, unit, *, actor="user", ducky="", at=None, conn=None) -> None
  group_open(group_id, plugin, *, actor="user", ducky="", message="", at=None) -> None
  group_close(group_id, *, at=None) -> None
  lock_request_add(account, space, plugin, unit, name="") -> None
  lock_request_get(account, space, plugin, unit) -> dict | None        → None
  pending_unsent(account, space) -> int                                → 0
  lost_choice_get(account, space) -> str                               → ""
  change_log(plugin) -> {epoch, changes, trimmed}                      → epoch 0, no changes
  incoming_queue_get(account, space, plugin) -> {count, by, ...}       → count 0, by []
  binding_get(plugin, *, now=None) -> dict | None                      → None
  forget_space(account, space) -> None                                 → None
  device_id() -> str   core's own (never delegated): this install's device id, as the
                       ledger contract names it.

``team_folders``
  note_ai_edit(paths, *, ducky="", at=None) -> list[str]               → []
  plugin_folders(plugin) -> list[Folder]                               → []
  folder_of(plugin, folder_id) -> Folder | None                        → None
  declarations(manifest) -> list[Folder]                               → []
  declaration_errors(manifest) -> list[str]                            → []
  offers(plugin) -> list[dict]                                         → []
  status(plugin, *, project_root=None) -> {folders, offers, note}      → all empty
  switch(plugin, scope_id, *, project_root=None, pull=None, push=None, first_share=False) -> dict
                                                                       → NO_ENGINE
  resolve(plugin, *, project_root=None) -> list[Binding]               → []
  team_copy(account, space, plugin, folder, project_id="") -> Path     → None
  _has_files(base, folder) -> bool                                     → False
  bind_plugin(plugin, *, project_root=None, wait_s=...) -> dict        → NO_ENGINE
  tree_unit(backend, space, folder_id, unit) -> str                    → unit
  sandbox_begin(plugin, scope, *, wait_s=...) -> int                   → 0
  sandbox_end(plugin) -> None                                          → None
  sandboxed(plugin) -> bool                                            → False
  call_marker(plugin, *, actor="user", ducky="") -> context manager    → nullcontext()
  live_calls(plugin) -> list[dict]                                     → []
  purge_space(account, space) -> int                                   → 0
  clear_space(account, space) -> list[str]                             → []
  erase_plugin(account, plugin) -> dict                                → NO_ENGINE
  remove_tree(path) -> bool   core's own: deletes a folder, junctions and links removed,
                              never followed (Local data needs it too).
  _lp(path) -> str            core's own: the Windows ``\\\\?\\`` long-path form.

``folder_files``  (the team folders' tables)
  files(account, space, plugin, folder_id, *, project_id=None, unit=None, conn=None) -> list[dict]  → []
  by_key(account, space, plugin, item_key, *, conn=None) -> dict | None                 → None
  reset_rows(conn, account, space, *, keep) -> int                     → 0
  requeue_refused(conn, account, space) -> int                         → 0
  requeue_blocked(account, space, plugin, folder_id=None, unit=None) -> int             → 0
  links(plugin=None) -> list[dict]                                     → []
  copy_get(account, space, plugin, folder_id, project_id="") -> dict   → {ready: 0, offer: 0}
  copy_put(account, space, plugin, folder_id, project_id="", **fields) -> None
  reload_epoch(plugin) -> int                                          → 0
  reload_reason(plugin) -> str                                         → ""

``live``
  deliver(account, space, plugin, sender, topic, data) -> None
  leave_all() -> None
  status(plugin, *, come_along=True) -> dict                           → LIVE_OFF
  group_devices(plugin, account, space) -> set[str] | None             → None
  forget(plugin, *, keep_live=False) -> None
  keeps_panels(plugin) -> bool                                         → False
  panels_kept(plugin) -> None
  join(plugin, room="", *, reload_panels=True) -> dict                 → NO_ENGINE
  team_of(plugin) -> (account, space) | None                           → None
  remember_team(account, space, choice) -> None
  leave_live(plugin) -> dict                                           → NO_ENGINE
  overview() -> {teams, ...}                                           → {"teams": []}
  join_team(account, space, room="", *, reload_panels=True) -> dict    → NO_ENGINE
  invite(account, space, device) -> dict                               → NO_ENGINE
  leave_team(account, space, *, why="", forget=True) -> dict           → NO_ENGINE
  send(plugin, topic, data, *, to="", fast=False) -> dict              → NO_ENGINE
  hold(plugin, anchor, on, t=0.0, view="") -> dict                     → NO_ENGINE
  holds_now(plugin) -> list[dict]                                      → []
  set_my_state(plugin, state) -> dict                                  → NO_ENGINE
  share(plugin, key, value) -> dict                                    → NO_ENGINE
  shared(plugin) -> dict                                               → {}
  member_states(plugin) -> list[dict]                                  → []
  scope_switched(plugin) -> None
  LiveApi(plugin_id) -> object   ``api.live`` for plugin backends      → a stand-in whose
                                 calls answer NO_ENGINE / empty

``sync_scheduler``
  run_round(account, space, *, force, pull=(), push_only=False) -> dict
                                                                       → NO_ENGINE + changed []
  wake(account, space, plugin=None) -> None
  space_active(account, space) -> bool                                 → False
  flush(timeout=...) -> int                                            → 0
  start() -> bool                                                      → False
  synced_event(out) -> dict   core's own: the push after a round.
  clamp_team_settings(delay, poll) -> (float, float)   core's own: the Team settings' ranges.

Constants stay in core (same values as the engine's): ``live.HOST_PREFIX``, ``live.START``,
``live.DATA_V``, ``team_folders.PULLING``, ``team_folders.TEST_RUNNING``,
``folder_files.LOCAL_ACCOUNT``, ``sync_scheduler.SLOW_STATES``, ``sync_scheduler.IDLE_EVERY_S``.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import os
import stat
import threading
import time
import uuid
from typing import Any, Callable

from backend.uefn_plugins.spaces import NO_PROVIDER_ERROR

_log = logging.getLogger(__name__)

NEEDS_ACCOUNT = "Team sync needs Ducky Account"
NO_ENGINE: dict[str, Any] = {"ok": False, "state": "no_provider", "error": NO_PROVIDER_ERROR, "text": NEEDS_ACCOUNT}
HEAD_DEFAULT: dict[str, Any] = {
    "seq": 0, "remote_seq": 0, "epoch": "", "engine": "v1", "state": "ok", "error": "", "attempts": 0,
    "next_try": 0.0, "moves": 0, "mode": "auto", "synced_at": 0.0,
}
LIVE_OFF: dict[str, Any] = {"supported": False, "on": False, "room": "", "state": "off", "peers": []}


def engine() -> Any | None:
    """The provider's engine, or None (no provider, or it has none / failed to answer)."""
    try:
        from backend.uefn_plugins import spaces

        return spaces.provider().engine()
    except Exception:  # noqa: BLE001 — a failing provider means no engine
        _log.debug("provider engine lookup failed", exc_info=True)
        return None


def status(account: str, space: str, plugin: str) -> dict[str, Any]:
    """One line of team-sync state for ``plugin`` in ``space``: the engine's ``status`` when
    it has one, else ``{"state": "no_provider", ...}`` ("Team sync needs Ducky Account")."""
    eng = engine()
    fn = getattr(eng, "status", None) if eng is not None else None
    if not callable(fn):
        return dict(NO_ENGINE)
    return fn(account, space, plugin)


# --------------------------------------------------------------------------- core's own helpers

_LONG = "\\\\?\\"
_LONG_UNC = "\\\\?\\UNC\\"
DEVICE_KEY = "space_device_id"


def _lp(path: Any) -> str:
    """``path`` as a ``\\\\?\\`` path on Windows, so long paths work."""
    s = os.fspath(path)
    if os.name != "nt" or s.startswith(_LONG):
        return s
    s = os.path.abspath(s)
    if s.startswith("\\\\"):
        return _LONG_UNC + s[2:]
    return _LONG + s


def _is_junction(p: str) -> bool:
    try:
        return os.path.isjunction(p)
    except (OSError, AttributeError):
        return False


def _unlink(p: str) -> None:
    try:
        os.unlink(p)
    except PermissionError:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        os.unlink(p)


def remove_tree(path: Any) -> bool:
    """Delete a folder: read-only files too, and a junction or link inside is removed
    itself (``os.rmdir`` on it), never followed. Returns whether everything went."""
    p = _lp(path)
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if _is_junction(p):
        try:
            os.rmdir(p)
            return True
        except OSError:
            return False
    if os.path.islink(p) or not stat.S_ISDIR(st.st_mode):
        try:
            _unlink(p)
            return True
        except OSError:
            return False
    ok = True
    try:
        with os.scandir(p) as it:
            entries = list(it)
    except OSError:
        return False
    for e in entries:
        try:
            if _is_junction(e.path):
                os.rmdir(e.path)
            elif e.is_symlink():
                os.unlink(e.path)
            elif e.is_dir(follow_symlinks=False):
                ok = remove_tree(e.path) and ok
            else:
                _unlink(e.path)
        except OSError:
            ok = False
    try:
        os.rmdir(p)
    except OSError:
        ok = False
    return ok


def device_id() -> str:
    """This install's device id: a random id made once and kept (never a hardware id).
    The first process to ask makes it; every process reads the same one."""
    from backend.store import db

    conn = db.connect()
    r = conn.execute("SELECT value FROM meta WHERE key=?", (DEVICE_KEY,)).fetchone()
    if r:
        return str(r[0])
    with db.write_txn(conn):
        conn.execute("INSERT OR IGNORE INTO meta(key, value, updated) VALUES (?, ?, ?)",
                     (DEVICE_KEY, uuid.uuid4().hex, time.time()))
    return str(conn.execute("SELECT value FROM meta WHERE key=?", (DEVICE_KEY,)).fetchone()[0])


def synced_event(out: dict[str, Any]) -> dict[str, Any]:
    """The push after a round: ``plugins`` whose data it changed (their bars refresh and
    their panels merge it), ``keys`` per plugin when known, ``synced`` (every bar refreshes)."""
    event: dict[str, Any] = {"type": "plugin_scope_changed", "plugins": [str(p) for p in out.get("changed") or []],
                             "synced": True}
    keys = out.get("keys")
    if isinstance(keys, dict) and keys:
        event["keys"] = {str(p): [str(k) for k in v] for p, v in keys.items() if p in event["plugins"]}
    return event


DEBOUNCE_S = 10.0
ACTIVE_EVERY_S = 60.0
TEAM_SEND_DELAY_RANGE = (0.0, 60.0)
TEAM_POLL_RANGE = (30.0, 600.0)


def clamp_team_settings(delay: Any, poll: Any) -> tuple[float, float]:
    """``(send delay, poll interval)`` in range; a value that isn't a number is its default."""
    def one(value: Any, lo: float, hi: float, default: float) -> float:
        try:
            got = float(value)
        except (TypeError, ValueError):
            return default
        return default if got != got else min(hi, max(lo, got))  # NaN

    return one(delay, *TEAM_SEND_DELAY_RANGE, DEBOUNCE_S), one(poll, *TEAM_POLL_RANGE, ACTIVE_EVERY_S)


_WANTED = {"start": False}  # the app asked for the scheduler (it starts once the engine is here)


def _start_scheduler() -> bool:
    """``sync_scheduler.start()``: the engine's, now or when the provider registers."""
    _WANTED["start"] = True
    eng = engine()
    fn = getattr(getattr(eng, "sync_scheduler", None), "start", None) if eng is not None else None
    return bool(fn()) if callable(fn) else False


def engine_arrived() -> None:
    """A provider registered: what plugins and the app asked for before the engine came
    happens now (live listeners, declared folders, the scheduler)."""
    with _PENDING_LOCK:
        pending = {pid: list(regs) for pid, regs in _PENDING.items()}
        _PENDING.clear()
    for pid, regs in pending.items():
        real = LiveApi(pid)._real()
        for name, args in regs if real is not None else ():
            try:
                getattr(real, name)(*args)
            except Exception:  # noqa: BLE001 — one plugin's listener never stops the others
                _log.warning("live listener of %s couldn't be added", pid, exc_info=True)
    try:
        from backend.uefn_plugins import host

        host.bind_folders_after_engine()
    except Exception:  # noqa: BLE001 — each plugin binds again on its next load
        _log.debug("binding folders after the engine came failed", exc_info=True)
    if _WANTED["start"]:
        _start_scheduler()


class _NoLiveApi:
    """``api.live`` with no engine: every call answers, nothing goes live."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def join(self, room: str = "") -> dict[str, Any]:
        return dict(NO_ENGINE)

    leave = set_my_state = share = send = lambda self, *a, **k: dict(NO_ENGINE)  # noqa: E731

    def on(self, *a: Any, **k: Any) -> None:
        return None

    on_shared = on_member_state = on

    def state(self) -> dict[str, Any]:
        return copy.deepcopy(LIVE_OFF)

    def member_states(self) -> list[dict[str, Any]]:
        return []

    def shared(self) -> dict[str, dict[str, Any]]:
        return {}


# Plugin team API (stable): renaming or removing any of these is a breaking change for plugins.
LIVE_API = ("join", "leave", "send", "on", "state", "set_my_state", "member_states", "on_member_state",
            "share", "shared", "on_shared")
_PENDING_LOCK = threading.Lock()
_PENDING: dict[str, list[tuple[str, tuple[Any, ...]]]] = {}  # plugin -> listeners added before the engine came


class LiveApi:
    """``api.live`` for plugin backends. Each call runs the engine's own ``api.live``,
    looked up at that moment, so a plugin that loaded before Ducky Account still goes live
    once it is here; listeners added before then are kept and added when it comes."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def _real(self) -> Any:
        eng = engine()
        cls = getattr(getattr(eng, "live", None), "LiveApi", None) if eng is not None else None
        return cls(self.plugin_id) if callable(cls) else None

    def _run(self, name: str, *args: Any, **kwargs: Any) -> Any:
        real = self._real()
        return getattr(real if real is not None else _NoLiveApi(self.plugin_id), name)(*args, **kwargs)

    def _listen(self, name: str, what: str, *args: Any) -> None:
        if not callable(args[-1]):
            raise TypeError(f"live.{name} needs a function: {what}")
        real = self._real()
        if real is not None:
            getattr(real, name)(*args)
            return
        with _PENDING_LOCK:
            _PENDING.setdefault(self.plugin_id, []).append((name, args))

    def join(self, room: str = "") -> dict[str, Any]:
        return self._run("join", room)

    def leave(self) -> dict[str, Any]:
        return self._run("leave")

    def send(self, topic: str, data: Any, to: str = "") -> dict[str, Any]:
        return self._run("send", topic, data, to=to)

    def on(self, topic: str, callback: Callable[[dict[str, str], Any], Any]) -> None:
        """``callback(sender, data)`` for each ``topic`` message from a teammate."""
        self._listen("on", "callback(sender, data)", topic, callback)

    def state(self) -> dict[str, Any]:
        return self._run("state")

    def set_my_state(self, state: dict[str, Any] | None) -> dict[str, Any]:
        """This PC's live state (a camera, a tool): every groupmate sees it (``member_states``)."""
        return self._run("set_my_state", state)

    def member_states(self) -> list[dict[str, Any]]:
        """Every groupmate's live state: ``[{from, state}]``."""
        return self._run("member_states")

    def on_member_state(self, callback: Callable[[dict[str, str], Any], Any]) -> None:
        """``callback(sender, state)`` when a groupmate's live state changes (None: gone)."""
        self._listen("on_member_state", "callback(sender, state)", callback)

    def share(self, key: str, value: Any) -> dict[str, Any]:
        """The group's value of ``key`` (None drops it), through its leader."""
        return self._run("share", key, value)

    def shared(self) -> dict[str, dict[str, Any]]:
        """The group's values now: ``{key: {value, by}}``."""
        return self._run("shared")

    def on_shared(self, callback: Callable[[str, Any, dict[str, Any]], Any]) -> None:
        """``callback(key, value, by)`` when one of the group's values changes."""
        self._listen("on_shared", "callback(key, value, by)", callback)


def _live_forget(plugin: str, *args: Any, **kwargs: Any) -> None:
    """``live.forget``: a plugin reloads or goes, so listeners it added before the engine
    came go too, and the engine forgets its own."""
    with _PENDING_LOCK:
        _PENDING.pop(plugin, None)
    eng = engine()
    fn = getattr(getattr(eng, "live", None), "forget", None) if eng is not None else None
    if callable(fn):
        fn(plugin, *args, **kwargs)


# --------------------------------------------------------------------------- the six areas


def _r(value: Any) -> Callable[..., Any]:
    """A default that answers a fresh copy of ``value`` whatever the arguments."""
    return lambda *a, **k: copy.deepcopy(value)


_NONE = _r(None)
_LIST = _r([])
_DICT = _r({})
_ZERO = _r(0)
_FALSE = _r(False)
_NO = _r(NO_ENGINE)
_COPY_FAIL = _r((False, NEEDS_ACCOUNT))


class _Area:
    """One engine module, seen through the provider: ``area.name(...)`` runs the engine's
    function, or the default when there is none. Unknown names raise AttributeError (typos)."""

    def __init__(self, name: str, defaults: dict[str, Callable[..., Any]], own: dict[str, Any]) -> None:
        self._name, self._defaults, self._own = name, defaults, own

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("__") or attr in ("_name", "_defaults", "_own"):
            raise AttributeError(attr)
        if attr in self._own:
            return self._own[attr]
        default = self._defaults.get(attr)
        if default is None:
            raise AttributeError(f"team_engine.{self._name} has no {attr}")
        return lambda *a, **k: self._call(attr, default, a, k)

    def _call(self, attr: str, default: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
        eng = engine()
        fn = getattr(getattr(eng, self._name, None), attr, None) if eng is not None else None
        if not callable(fn):
            return default(*args, **kwargs)
        return fn(*args, **kwargs)

    def names(self) -> list[str]:
        """Every delegated function name (for tests and the plugin's own check)."""
        return sorted(self._defaults)


vcs_engine = _Area("vcs_engine", {
    "unit_label": lambda account, space, plugin, unit, *a, **k: unit,
    "my_user_id": _r(""),
    "other_pc": _r(""),
    "lock_ducky": _NONE,
    "holder_of": _r(("", "")),
    "_held_items": _LIST,
    "after_pull": _NONE,
    "apply_waiting": _NO,
    "pull_copy": _COPY_FAIL,
    "send_copy": _COPY_FAIL,
    "send_before_leaving": _LIST,
    "resolve_unit": _NO,
    "folder_rows": _LIST,
    "folder_outgoing": _LIST,
    "run": _NO,
    "set_mode": _NO,
    "acquire": lambda account, space, plugin, backend, units, *a, **k: {u: dict(NO_ENGINE) for u, _ in units},
    "release": _NO,
    "break_lock": _NO,
    "ask": _NO,
    "device_names": _DICT,
    "resolve": _NO,
    "restore": _NO,
    "undo": _NO,
    "history": _r({**NO_ENGINE, "entries": []}),
    "save_unsent_to_local": _NO,
    "conflicts_into_rows": _ZERO,
    "discard_unsent": _NO,
}, {})

space_vcs = _Area("space_vcs", {
    "row": _NONE,
    "head_get": _r(HEAD_DEFAULT),
    "cs_items_open": _r(set()),
    "conflict_get": _NONE,
    "conflicts": _LIST,
    "blocked": _LIST,
    "blocked_retry_now": _FALSE,
    "locks": _LIST,
    "foreign_lock": _NONE,
    "lock_delete": _NONE,
    "presence_get": _r({"roster": {}, "updated": 0.0, "sent_sha": "", "sent_at": 0.0}),
    "activity_note": _NONE,
    "group_open": _NONE,
    "group_close": _NONE,
    "lock_request_add": _NONE,
    "lock_request_get": _NONE,
    "pending_unsent": _ZERO,
    "lost_choice_get": _r(""),
    "change_log": _r({"epoch": 0, "changes": [], "trimmed": 0}),
    "incoming_queue_get": _r({"count": 0, "by": []}),
    "binding_get": _NONE,
    "forget_space": _NONE,
}, {"device_id": device_id})

team_folders = _Area("team_folders", {
    "note_ai_edit": _LIST,
    "plugin_folders": _LIST,
    "folder_of": _NONE,
    "declarations": _LIST,
    "declaration_errors": _LIST,
    "offers": _LIST,
    "status": _r({"folders": [], "offers": [], "note": ""}),
    "switch": _NO,
    "resolve": _LIST,
    "team_copy": _NONE,
    "_has_files": _FALSE,
    "bind_plugin": _NO,
    "tree_unit": lambda backend, space, folder_id, unit, *a, **k: unit,
    "sandbox_begin": _ZERO,
    "sandbox_end": _NONE,
    "sandboxed": _FALSE,
    "call_marker": lambda *a, **k: contextlib.nullcontext(),
    "live_calls": _LIST,
    "purge_space": _ZERO,
    "clear_space": _LIST,
    "erase_plugin": _NO,
}, {"remove_tree": remove_tree, "_lp": _lp,
    "PULLING": "Getting {team}'s files… {pct}%",
    "TEST_RUNNING": "A test of {plugin} is running. Try again when it finishes."})

folder_files = _Area("folder_files", {
    "files": _LIST,
    "by_key": _NONE,
    "reset_rows": _ZERO,
    "requeue_refused": _ZERO,
    "requeue_blocked": _ZERO,
    "links": _LIST,
    "copy_get": _r({"ready": 0, "offer": 0}),
    "copy_put": _NONE,
    "reload_epoch": _ZERO,
    "reload_reason": _r(""),
}, {"LOCAL_ACCOUNT": "_pc"})

live = _Area("live", {
    "deliver": _NONE,
    "leave_all": _NONE,
    "status": _r(LIVE_OFF),
    "group_devices": _NONE,
    "keeps_panels": _FALSE,
    "panels_kept": _NONE,
    "join": _NO,
    "team_of": _NONE,
    "remember_team": _NONE,
    "leave_live": _NO,
    "overview": _r({"teams": []}),
    "join_team": _NO,
    "invite": _NO,
    "leave_team": _NO,
    "send": _NO,
    "hold": _NO,
    "holds_now": _LIST,
    "set_my_state": _NO,
    "share": _NO,
    "shared": _DICT,
    "member_states": _LIST,
    "scope_switched": _NONE,
}, {"LiveApi": LiveApi, "forget": _live_forget, "HOST_PREFIX": "h:", "START": "start", "DATA_V": 3})

sync_scheduler = _Area("sync_scheduler", {
    "run_round": _r({**NO_ENGINE, "changed": []}),
    "wake": _NONE,
    "space_active": _FALSE,
    "flush": _ZERO,
}, {"start": _start_scheduler, "synced_event": synced_event, "clamp_team_settings": clamp_team_settings,
    "SLOW_STATES": frozenset({"lost", "reset"}), "IDLE_EVERY_S": 15 * 60.0})

AREAS = (vcs_engine, space_vcs, team_folders, folder_files, live, sync_scheduler)
