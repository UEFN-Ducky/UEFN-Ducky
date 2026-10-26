"""Plugin data scopes + the host data service.

A scope is ``(account, personal | space_id)``:

- the account is the signed-in DuckyOS user (a stable hash of site + email), or
  ``_local`` when signed out;
- where a plugin's data lives is picked per plugin (``plugin_scopes``): Local (scope
  ``personal``) or exactly one shared space (:mod:`backend.uefn_plugins.spaces`). No
  pick → Local; a space that can't be used now stays on screen, read-only with the
  reason. Spaces never share rows: switching shows that scope's own copy, and the only
  way data moves is an explicit copy of the Local copy into one space
  (:func:`copy_local_into`). A plugin whose manifest says ``"data_sharing": false`` is
  always Local (:func:`sharing_allowed`).

Plugins never pick their own folder or rows: docs (one JSON doc per entity) and
files go through :class:`PluginData`, which writes the active scope only. Rows
and folders are keyed by account and sealed for it (:mod:`data_crypto`), so
another account on this PC can't read them. Changes to a shared space's copy are
queued (``dirty``) for the spaces provider to sync; Personal and ``sensitive`` data
never leave the PC, and neither do the items a manifest names as this PC's own
(``"data_local"`` prefixes: they stay in Local whatever copy the plugin shows). A plugin's
cache and prefs rows (``plugin_kv``) follow the same scope but never sync: each space has
its own on this PC.

:func:`sandbox_begin` points one plugin at a throwaway scope for a plugin test: the
test's own calls and the threads they start, that plugin only, so the test never
touches the user's data and never hides it from anyone else.

Writes to a shared copy also do what a team copy needs (:func:`write_group`): each plugin
tool call or panel call is one write group (one changeset, with who made it); a write to
an item someone else holds a lock on is refused with their name; a ``data_locks`` prefix
of the manifest takes a lock first; and what changed here is noted for presence.
"""

from __future__ import annotations

import contextlib
import contextvars
import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

LOCAL = "_local"
PERSONAL = "personal"
DOC_MAX_BYTES = 1024 * 1024
# How long a plugin's first read of a never-pulled shared space waits for the pull.
FIRST_PULL_WAIT_S = 10.0
ASSET_MAX_BYTES = 100 * 1024 * 1024
# What sealing adds to a shared doc or file: the server limits the sealed size, so shared
# copies are refused up front this much below the Local limits.
SHARED_SEAL_OVERHEAD = 33
# Throwaway scopes of plugin tests (:func:`sandbox_begin`). The test copy belongs to the
# test: its calls carry a token (``_SANDBOX_CTX``, inherited by the threads they start),
# everyone else's calls read their real data. Oct 12 2026: when it was process-wide, a
# shared space's live projects and requests "disappeared" whenever one agent ran the full test.
# A thread a test started reads the real data again once the test ends (its token is gone).
SANDBOX_PREFIX = "_plugin_test_"
_SANDBOXES: dict[str, dict[str, Any]] = {}  # token → scope, while its test runs
_TESTING: dict[str, str] = {}  # plugin → token of the test running now
_SANDBOX_CTX: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("plugin_test_sandbox", default={})
_SANDBOX_LOCK = threading.Lock()
_inherit_installed = False

_PLUGIN_RE = re.compile(r"^[a-z0-9._-]{1,64}$")
_DOC_RE = re.compile(r"^[a-z0-9._-]{1,128}$")
_ASSET_RE = re.compile(r"^[a-z0-9._/-]{1,256}$")
_SPACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ReadOnlyScope(PermissionError):
    """The scope can't be written from this PC right now (the message says why)."""


class LockedOut(ReadOnlyScope):
    """A write refused because someone else holds a lock on the item. ``lock``: ``unit``,
    ``name``, ``holder`` (their name) and ``since`` (epoch seconds)."""

    def __init__(self, message: str, lock: dict[str, Any]) -> None:
        super().__init__(message)
        self.lock = lock


# --------------------------------------------------------------------------- ids


def valid_plugin_id(pid: str) -> bool:
    return bool(_PLUGIN_RE.match(pid or "")) and not pid.startswith(".") and ".." not in pid


def valid_doc_key(key: str) -> bool:
    return bool(_DOC_RE.match(key or "")) and not key.startswith(".") and ".." not in key


def valid_asset_path(path: str) -> bool:
    """``[a-z0-9._/-]`` ≤256, every segment non-empty, none starting with a dot, never ``..``."""
    return (
        bool(_ASSET_RE.match(path or ""))
        and ".." not in path
        and all(seg and not seg.startswith(".") for seg in path.split("/"))
    )


def valid_space_id(space: str) -> bool:
    return bool(_SPACE_RE.match(space or "")) and space not in (PERSONAL, LOCAL)


def _need(ok: bool, what: str) -> None:
    if not ok:
        raise ValueError(f"invalid {what}")


# --------------------------------------------------------------------------- scope


def account_id_for(key: str) -> str:
    """Row/folder id of an account key (``site|email``); ``_local`` when signed out."""
    return LOCAL if not key else "a_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def account_id() -> str:
    """Stable per-account id for rows and folders; ``_local`` when signed out.
    ``account_key()`` re-reads the login only when ``meta.login_version`` changed (one
    meta read per call), so the MCP bridge still follows an account switch made in the app."""
    from frontend.duckyos_account import account_key

    aid = account_id_for(account_key())
    _claim_legacy(aid)
    return aid


# (db path, db generation, account) found with nothing left to claim. A restore or an
# import bumps the generation, a login change clears it: then the next call checks again.
_CLAIMED: set[tuple[str, int, str]] = set()


def forget_claims() -> None:
    _CLAIMED.clear()


def _claim_legacy(aid: str) -> None:
    """Rows from before scopes (migration 0009) join the first account that opens them,
    and so do chats and duckies from before they were per account (migration 0024).
    Checked once per account and database generation, not on every call."""
    from backend.store import account_rows, db
    from backend.store.repos import plugin_kv

    memo = (str(db.db_path()), db.generation(), aid)
    if memo in _CLAIMED:
        return
    if plugin_kv.has_unclaimed():
        plugin_kv.claim_unclaimed(aid)
    try:
        account_rows.claim(aid)
    except Exception:  # noqa: BLE001 — never in the way of the account id; the next call tries again
        _log.exception("claiming chats and duckies from before accounts failed")
        return
    _CLAIMED.add(memo)


def _locked(account: str) -> bool:
    """Signed in, but this account's data key isn't on the PC yet (offline first sign-in)."""
    from backend.uefn_plugins.data_crypto import available

    return not available(account)


def personal_scope(account: str | None = None) -> dict[str, Any]:
    account = account or account_id()
    locked = _locked(account)
    # Shown as "Local": it never leaves this PC (the id stays "personal").
    return {"account": account, "id": PERSONAL, "kind": "personal", "label": "Local",
            "spaceId": "", "readOnly": locked, "state": "locked" if locked else "ok"}


def shared_scope(account: str, space: str) -> dict[str, Any]:
    """A shared space's scope, from its sync row (the spaces provider keeps it). ``waiting``:
    never pulled on this PC for this account, so it is read-only until the first pull
    lands — a plugin must not seed its empty-state defaults over the space's data.
    ``locked``: the account's data key is missing. ``lost``: access to the space is gone;
    its copy here is unreadable and read-only until access comes back. ``unavailable``
    (the provider can't use the space now) and ``reset`` (its data was reset on the server)
    are read-only; ``reason`` says why. ``no_provider``: no provider is loaded, so nothing
    syncs; writes still queue."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.spaces import STATES_READ_ONLY, has_provider, provider

    sync = repo.sync_get(account, space)
    state = sync["state"]
    if state == "lost":
        # The provider may drop a copy whose access has been gone long enough.
        state = provider().settle_lost(account, space) or "lost"
    if state not in ("unavailable", "lost") and _locked(account):
        state = "locked"
    elif not sync["synced_at"] and state not in ("paused", "unavailable", "lost"):
        state = "waiting"
    elif state not in STATES_READ_ONLY and not has_provider():
        state = "no_provider"
    return {"account": account, "id": space, "kind": "shared", "label": sync["label"] or "Shared", "spaceId": space,
            "readOnly": state in STATES_READ_ONLY, "state": state, "reason": str(sync["error"] or "")}


def space_access(space: str) -> tuple[bool, str]:
    """(may use, label) for a shared space of the signed-in account: private compiled
    plugins published to it load only while this holds (paused data is still access)."""
    account = account_id()
    if account == LOCAL or not valid_space_id(space):
        return False, space
    scope = shared_scope(account, space)
    # No provider loaded, or the space's data reset on the server: membership is unchanged.
    return scope["state"] in ("ok", "paused", "no_provider", "reset"), str(scope["label"] or space)


_SHARING: dict[str, tuple[float, Any]] = {}  # manifest path → (mtime, its "data_sharing" value)


def _data_sharing(plugin: str) -> Any:
    """The manifest's ``"data_sharing"`` value; None when unset or there is no manifest
    (core data such as workflows). Read per data call, so cached by the manifest's mtime."""
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, load_plugin_manifest, plugin_dir

    try:
        path = plugin_dir(plugin) / PLUGIN_MANIFEST
        mtime = path.stat().st_mtime
    except (OSError, ValueError):
        return None
    hit = _SHARING.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    flag = (load_plugin_manifest(plugin) or {}).get("data_sharing")
    _SHARING[str(path)] = (mtime, flag)
    return flag


_LIVE_DATA: dict[str, tuple[float, bool, bool]] = {}  # manifest path → (mtime, data live, asked for in it)


def data_live(plugin: str) -> bool:
    """Whether the plugin's changes go to the live group at once over its link
    (:func:`store.data_live`: every shared plugin's, unless its manifest says
    ``"data_live": false``). Read per write, so cached by the manifest's mtime."""
    return _manifest_live(plugin)[0]


def data_live_asked(plugin: str) -> bool:
    """The manifest says ``"data_live": true`` itself: apps before every plugin's data went
    live (live version :data:`live.DATA_V`) send this plugin's for a groupmate too."""
    return _manifest_live(plugin)[1]


def _manifest_live(plugin: str) -> tuple[bool, bool]:
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, data_live as manifest_live, load_plugin_manifest, plugin_dir

    try:
        path = plugin_dir(plugin) / PLUGIN_MANIFEST
        mtime = path.stat().st_mtime
    except (OSError, ValueError):
        return False, False
    hit = _LIVE_DATA.get(str(path))
    if hit is None or hit[0] != mtime:
        manifest = load_plugin_manifest(plugin) or {}
        hit = (mtime, manifest_live(manifest), manifest.get("data_live") is True)
        _LIVE_DATA[str(path)] = hit
    return hit[1], hit[2]


def _live_group(scope: dict[str, Any], plugin: str) -> set[str] | None:
    """The other PCs of this PC's live group in ``plugin`` on this team copy (any process:
    the app publishes its room), when the plugin shares its data live; else None."""
    if not data_live(plugin):
        return None
    try:
        from backend.uefn_plugins import live

        return live.group_devices(plugin, scope["account"], scope["id"])
    except Exception:  # noqa: BLE001 — not live as far as anyone can tell
        _log.debug("live group of %s unknown", plugin, exc_info=True)
        return None


_LOCKS: dict[str, tuple[float, dict[str, str]]] = {}  # manifest path → (mtime, its valid "data_locks")


def data_lock_policy(plugin: str, key: str) -> str:
    """``auto``, ``required`` or '' for a doc key or file path of ``plugin``. Every plugin's
    shared items lock by default (``auto``): the first change takes the lock, and the item
    stays the person's until 30 s after their last change went out, so two people never
    overwrite each other. The manifest's ``"data_locks": {"<key prefix>": "auto" |
    "required" | "none"}`` changes that per prefix, the longest prefix winning (``none``:
    no lock). '' for data no plugin manifest describes (Ducky's own workflows). Entries
    that aren't valid are ignored (``ducky_plugin_validate`` names them)."""
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, data_locks, load_plugin_manifest, plugin_dir

    try:
        path = plugin_dir(plugin) / PLUGIN_MANIFEST
        mtime = path.stat().st_mtime
    except (OSError, ValueError):
        return ""
    hit = _LOCKS.get(str(path))
    if not hit or hit[0] != mtime:
        hit = (mtime, data_locks(load_plugin_manifest(plugin) or {}))
        _LOCKS[str(path)] = hit
    matches = [prefix for prefix in hit[1] if key.startswith(prefix)]
    policy = hit[1][max(matches, key=len)] if matches else "auto"
    return "" if policy == "none" else policy


def item_label(value: Any, key: str) -> str:
    """What people call an item: a doc's own ``name``, ``title`` or ``label`` field
    ("Untitled island 1", not ``project.mvbtpazj``), a file's name, else its key."""
    if isinstance(value, dict):
        for field in ("name", "title", "label"):
            text = value.get(field)
            if isinstance(text, str) and text.strip():
                return text.strip()[:80]
    if "/" in key:
        return key.rsplit("/", 1)[-1] or key
    return key


_LOCAL_PREFIXES: dict[str, tuple[float, tuple[str, ...]]] = {}  # manifest path → (mtime, its "data_local")


def local_prefixes(plugin: str) -> tuple[str, ...]:
    """The manifest's ``"data_local": ["<key prefix>", ...]``: docs and files of this PC
    only (diagnostics, per-device state). Whatever copy the plugin shows, they live in its
    Local copy, never sync and never count as team activity; a team copy's own items under
    them are never read. Read per data call, so cached by the manifest's mtime."""
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, data_local, load_plugin_manifest, plugin_dir

    try:
        path = plugin_dir(plugin) / PLUGIN_MANIFEST
        mtime = path.stat().st_mtime
    except (OSError, ValueError):
        return ()
    hit = _LOCAL_PREFIXES.get(str(path))
    if not hit or hit[0] != mtime:
        hit = (mtime, data_local(load_plugin_manifest(plugin) or {}))
        _LOCAL_PREFIXES[str(path)] = hit
    return hit[1]


def is_local_key(plugin: str, key: str) -> bool:
    """``key`` (a doc key or file path) is one of this PC's own items (:func:`local_prefixes`)."""
    return any(key.startswith(prefix) for prefix in local_prefixes(plugin))


def sharing_allowed(plugin: str) -> bool:
    """False when the plugin's manifest says ``"data_sharing": false``: its data always
    stays Local on this PC, whatever was picked before, and nothing offers it a shared
    space."""
    return _data_sharing(plugin) is not False


def shareable(plugin: str) -> bool:
    """Whether a shared space is worth offering ``plugin``: only data that goes through the
    host data service, or a folder the plugin declares (``team_folders``), syncs. True when
    the plugin has data rows, used ``api.data`` in this process, declares
    ``"data_sharing": true`` or team folders, or is core data (``ducky.*``, such as
    workflows). A plugin that keeps its own files without declaring them gets False, and
    ``"data_sharing": false`` always wins."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import host
    from backend.uefn_plugins.team_folders import plugin_folders

    flag = _data_sharing(plugin)
    if flag is False:
        return False
    if flag is True or plugin.startswith("ducky.") or plugin in host._DATA_PLUGINS:
        return True
    if plugin_folders(plugin):
        return True
    return repo.has_rows(account_id(), plugin)


def _refuse_sharing(plugin: str) -> None:
    if not sharing_allowed(plugin):
        from backend.uefn_plugins.spaces import plugin_label

        raise ValueError(f"{plugin_label(plugin) or plugin} keeps its data on this PC only. It can't be shared.")


def active_scope(plugin: str) -> dict[str, Any]:
    """The scope ``plugin``'s data reads and writes right now."""
    from backend.store.repos import plugin_data as repo

    boxed = _boxed(plugin)
    if boxed is not None:
        return dict(boxed)
    account = account_id()
    if account == LOCAL or not sharing_allowed(plugin):
        return personal_scope(account)
    space = repo.link_get(account, plugin)
    if space == PERSONAL or not valid_space_id(space):
        return personal_scope(account)
    scope = shared_scope(account, space)
    if scope["state"] == "unavailable" and not repo.has_rows(account, plugin, space):
        # The provider already dropped this plugin's copy here: Local, no teaser. A copy
        # still here stays on screen, read-only with the reason.
        return personal_scope(account)
    return scope


def _boxed(plugin: str) -> dict[str, Any] | None:
    """The test copy this call reads, when it is part of a running test of ``plugin``."""
    token = _SANDBOX_CTX.get().get(plugin)
    return _SANDBOXES.get(token) if token else None


def sandbox_begin(plugin: str) -> dict[str, Any]:
    """From now until :func:`sandbox_end`, this caller's reads and writes of ``plugin``'s
    docs, files, cache and prefs (Local, shared or ``sensitive``), and those of the threads
    it starts, go to a throwaway scope that starts empty. Everyone else keeps the real
    data. A plugin that keeps its own folders is the exception: a folder's junction is
    every process's, so while its test runs, the plugin's calls from outside the test are
    refused (:func:`write_group`). Raises ``ValueError`` while another test of the plugin
    is running."""
    import uuid

    from backend.uefn_plugins import team_folders

    _need(valid_plugin_id(plugin), "plugin id")
    account = account_id()
    token = uuid.uuid4().hex
    scope_id = SANDBOX_PREFIX + hashlib.sha256(plugin.encode("utf-8")).hexdigest()[:8] + token[:8]
    scope = {"account": account, "id": scope_id, "kind": "sandbox", "label": "Plugin test", "spaceId": "",
             "readOnly": False, "state": "ok"}
    with _SANDBOX_LOCK:
        if plugin in _TESTING:
            if team_folders.plugin_folders(plugin):
                raise ValueError(
                    f"A test of {plugin} is already running, and while it runs the plugin's own folders are the "
                    "test's: its other calls are refused. Try again when it finishes."
                )
            raise ValueError(
                f"A test of {plugin} is already running. Its test copy is its own (everyone else's calls "
                "keep their real data), so don't wait for it: check your own work with "
                "ducky_plugin_validate and live calls, and leave full tests to one agent."
            )
        _TESTING[plugin] = token
        _SANDBOXES[token] = scope
    _SANDBOX_CTX.set({**_SANDBOX_CTX.get(), plugin: token})
    _inherit_into_threads()
    try:
        purge_space(account, scope_id)
        # Folders the plugin keeps itself point at the test's copies too (a junction is
        # every process's): its calls from outside the test are refused meanwhile.
        team_folders.sandbox_begin(plugin, scope)
    except BaseException:
        sandbox_end(plugin)
        raise
    return dict(scope)


def sandbox_end(plugin: str) -> None:
    """The test is over: its throwaway scope and rows go, the plugin's folders point at
    its real copies again, and threads it started read the real data again."""
    with _SANDBOX_LOCK:
        token = _TESTING.pop(plugin, "")
        scope = _SANDBOXES.pop(token, None) if token else None
    mine = _SANDBOX_CTX.get()
    if plugin in mine:
        _SANDBOX_CTX.set({k: v for k, v in mine.items() if k != plugin})
    try:
        from backend.uefn_plugins import team_folders

        team_folders.sandbox_end(plugin)
    except Exception:  # noqa: BLE001 — the next bind puts them back
        _log.warning("putting %s's folders back after its test failed", plugin, exc_info=True)
    if scope is not None:
        purge_space(scope["account"], scope["id"])


def _agent_call() -> bool:
    """An agent's plugin call is running here (its write group is open)."""
    return any(not g.closed and g.actor == "agent" for g in _GROUPS.get().values())


def _inherit_into_threads() -> None:
    """Threads and pool tasks started inside a test, or inside an agent's plugin call, run
    in that context: what the plugin does on them lands in the test copy too, and writes as
    that agent with its ducky's name (a render job that keeps writing after the call
    returned; before, it said only "AI", or the person in the app). A pool's worker thread
    itself is never bound to either: only the task is."""
    global _inherit_installed
    with _SANDBOX_LOCK:
        if _inherit_installed:
            return
        _inherit_installed = True
    from concurrent.futures import ThreadPoolExecutor

    thread_init = threading.Thread.__init__
    pool_submit = ThreadPoolExecutor.submit

    def carried() -> bool:
        return bool(_SANDBOX_CTX.get()) or _agent_call()

    def init(self: threading.Thread, *args: Any, **kwargs: Any) -> None:
        thread_init(self, *args, **kwargs)
        if carried():
            ctx = contextvars.copy_context()
            run = self.run
            self.run = lambda: ctx.run(run)  # type: ignore[method-assign]

    def submit(self: ThreadPoolExecutor, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        if not carried():
            return pool_submit(self, fn, *args, **kwargs)
        ctx = contextvars.copy_context()
        unbound = _SANDBOX_CTX.set({})  # a worker this starts serves every caller
        no_call = _GROUPS.set({})
        try:
            return pool_submit(self, ctx.run, fn, *args, **kwargs)
        finally:
            _GROUPS.reset(no_call)
            _SANDBOX_CTX.reset(unbound)

    threading.Thread.__init__ = init  # type: ignore[method-assign]
    ThreadPoolExecutor.submit = submit  # type: ignore[method-assign]


@contextlib.contextmanager
def sandbox(plugin: str):
    """:func:`sandbox_begin` … :func:`sandbox_end` around a block."""
    scope = sandbox_begin(plugin)
    try:
        yield scope
    finally:
        sandbox_end(plugin)


def sandboxed_plugins() -> frozenset[str]:
    """Plugins this caller is testing right now (their data is its test copy)."""
    return frozenset(plugin for plugin in _SANDBOX_CTX.get() if _boxed(plugin) is not None)


def _personal_for(plugin: str) -> dict[str, Any]:
    """The Local scope ``plugin`` writes ``sensitive`` docs to (its test scope while under test)."""
    boxed = _boxed(plugin)
    return dict(boxed) if boxed is not None else personal_scope()


def scope_view(scope: dict[str, Any]) -> dict[str, Any]:
    """What panels and tool results see: label, kind, read-only (no account id)."""
    return {"kind": scope["kind"], "label": scope["label"], "spaceId": scope["spaceId"], "readOnly": scope["readOnly"]}


def scope_name(scope: dict[str, Any]) -> str:
    """``<kind> <label>`` / ``local`` — how tool results name the copy they changed."""
    if scope["kind"] != "shared":
        return "local"
    from backend.uefn_plugins.spaces import provider

    return f"{provider().kind_label} {scope['label']}"


def link_plugin(plugin: str, scope_id: str) -> dict[str, Any]:
    """Keep ``plugin``'s data in Local or in one of the account's shared spaces. Nothing
    moves: the plugin shows that scope's own copy from now on."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.spaces import provider

    _need(valid_plugin_id(plugin), "plugin id")
    account = account_id()
    if account == LOCAL:
        raise ValueError("Sign in to share a plugin's data")
    if scope_id != PERSONAL:
        _need(valid_space_id(scope_id), "space id")
        _refuse_sharing(plugin)
        sync = repo.sync_get(account, scope_id)
        if sync["state"] == "unavailable":
            # A fresh link tries again even if an earlier round found the space unavailable.
            repo.sync_put(account, scope_id, state="ok", error="")
        if sync["cursor_rev"] and not repo.has_rows(account, plugin, scope_id):
            # Nothing of the plugin here although the space was pulled (its data was erased
            # with an uninstall): the next round pulls from the start and brings it back.
            repo.sync_put(account, scope_id, cursor_rev=0)
    repo.link_set(account, plugin, scope_id)
    if scope_id != PERSONAL:
        provider().linked(account, plugin, scope_id)
    return active_scope(plugin)


def copy_local_into(plugin: str, space: str) -> dict[str, Any]:
    """One way, on request: the plugin's Local docs and files go into ``space``'s copy
    (queued to sync). An item of the space with the same key is kept as it is.
    Sensitive docs and the plugin's own items of this PC (``data_local``) stay Local, and
    so do items too big to share once sealed (listed in
    ``tooBig``: the server would refuse them). Space → space never happens."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.data_crypto import open_bytes, open_text, seal_bytes, seal_text
    from backend.uefn_plugins.spaces import provider

    _need(valid_plugin_id(plugin), "plugin id")
    _need(valid_space_id(space), "space id")
    _refuse_sharing(plugin)
    account = account_id()
    if account == LOCAL:
        raise ValueError("Sign in to share a plugin's data")
    src = personal_scope(account)
    target = shared_scope(account, space)
    if target["state"] == "waiting":
        # Never pulled on this PC: get the space's copy first, so "keep theirs" is true.
        provider().first_pull(account, space, FIRST_PULL_WAIT_S)
        target = shared_scope(account, space)
    dst = PluginData(plugin)._writable(target)
    copied = kept = 0
    too_big: list[str] = []
    for kind in ("doc", "asset"):
        limit = (DOC_MAX_BYTES if kind == "doc" else ASSET_MAX_BYTES) - SHARED_SEAL_OVERHEAD
        for row in repo.rows(account, PERSONAL, plugin, kind):
            if row["sensitive"] or is_local_key(plugin, row["key"]):
                continue
            old = repo.get(account, space, plugin, kind, row["key"])
            if old and not old["deleted"]:
                kept += 1
                continue
            if int(row["size"]) > limit:
                too_big.append(row["key"])
                continue
            if kind == "doc":
                if row["value"] is None:
                    continue
                sealed = seal_text(open_text(row["value"], account), account)
                repo.put(account, space, plugin, kind, row["key"], value=sealed, size=int(row["size"]),
                         sha256=row["sha256"], dirty=True)
            else:
                source = asset_file(src, plugin, row["key"])
                if not source.is_file():
                    continue
                _write_asset(dst, plugin, row["key"], seal_bytes(open_bytes(source.read_bytes(), account), account),
                             size=int(row["size"]), sha256=row["sha256"], dirty=True)
            copied += 1
    return {"ok": True, "copied": copied, "kept": kept, "tooBig": too_big}


def scopes_root(account: str) -> Path:
    """``%LOCALAPPDATA%/UEFN-Ducky/scopes/{account}``, on Windows as an extended-length
    (``\\\\?\\``) path: a team file's key folders and temp name run past MAX_PATH (260), and
    every path under here works then whatever the registry or the EXE's manifest says.
    Paths stored before (unprefixed) name the same files; compare them as team_folders
    does (``_key``)."""
    from backend.uefn_plugins.team_folders import _lp
    from frontend.app_paths import resolve_app_data_dir

    return Path(_lp(resolve_app_data_dir() / "scopes" / account))


def plugin_dir(scope: dict[str, Any], plugin: str) -> Path:
    """``%LOCALAPPDATA%/UEFN-Ducky/scopes/{account}/{personal|space_id}/{plugin_id}/``."""
    _need(valid_plugin_id(plugin), "plugin id")
    return scopes_root(scope["account"]) / scope["id"] / plugin


def purge_space(account: str, space: str) -> None:
    """A shared space's copy on this PC goes, rows and files (the provider decides when,
    e.g. a while after access was lost). Never anyone else's copy: a plugin folder pointing
    into it goes back to Local first, and links inside are removed, never followed."""
    from backend.store.repos import plugin_data as repo
    from backend.store.repos import plugin_kv
    from backend.uefn_plugins import team_folders

    if not valid_space_id(space):
        return
    from backend.store.repos import workflows

    from backend.store.repos import space_vcs

    space_workflows = [r["key"] for r in repo.rows(account, space, "ducky.automations", "doc")]
    workflows.runtime_delete(account, space_workflows)
    repo.delete_scope(account, space)
    plugin_kv.delete_scope(account, space)
    space_vcs.forget_space(account, space)  # its heads, changesets, conflicts, locks and merge bases
    try:  # plugins' api.search records of this copy go with its other data
        from backend.store.repos import search as search_repo

        search_repo.drop_scope_records(account, space)
    except Exception:  # noqa: BLE001 — a search error never blocks the purge
        pass
    try:  # folder copies: junctions into them go back to Local, then the copies and their rows
        team_folders.purge_space(account, space)
    except Exception:  # noqa: BLE001 — the copy below still goes
        _log.warning("purging the folder copies of %s failed", space, exc_info=True)
    team_folders.remove_tree(scopes_root(account) / space)


def erase_plugin(plugin: str) -> None:
    """Uninstall + erase data: this account's docs and files of ``plugin`` in every scope,
    and its link (a reinstall shows Local). Later rounds of those spaces don't bring the
    copy back; linking the plugin to one again does (:func:`link_plugin`). Folders it
    keeps itself lose their junction (the Local copy goes back in its place); this
    account's team copies of them go, never a copy another scope still uses."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import team_folders

    account = account_id()
    try:
        team_folders.erase_plugin(account, plugin)
    except Exception:  # noqa: BLE001 — the plugin's data rows still go
        _log.warning("erasing the folders of %s failed", plugin, exc_info=True)
    repo.delete_plugin(account, plugin)
    root = scopes_root(account)
    if root.is_dir():
        for scope_dir in root.iterdir():
            team_folders.remove_tree(scope_dir / plugin)


# --------------------------------------------------------------------------- host data API


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode_doc(value: Any) -> bytes:
    """Canonical doc bytes: what is hashed, pushed and stored."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def asset_file(scope: dict[str, Any], plugin: str, path: str) -> Path:
    _need(valid_asset_path(path), "file path")
    return plugin_dir(scope, plugin) / "files" / Path(*path.split("/"))


def write_asset_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(target)


def _write_asset(scope: dict[str, Any], plugin: str, path: str, sealed: bytes, *, size: int, sha256: str,
                 dirty: bool, group_id: str = "") -> None:
    """A local file write: the sealed bytes go to a uuid temp file next to the target, then
    the file and its row land in one transaction (nobody sees one without the other)."""
    from backend.store.repos import plugin_data as repo

    target = asset_file(scope, plugin, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temp.write_bytes(sealed)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    repo.put_file_row(scope["account"], scope["id"], plugin, path, temp_path=temp, target_path=target, size=size,
                      sha256=sha256, dirty=dirty, group_id=group_id)


def _closed(scope: dict[str, Any]) -> bool:
    """Access to the space is lost: its copy here reads as empty until access comes back."""
    return scope["state"] == "lost"


# --------------------------------------------------------------------------- write groups + team writes
#
# One plugin tool call, panel RPC or panel data call is one write group: what it writes to
# a shared copy goes out as one changeset, named after the call and made by the person or
# by an agent (with its ducky's name). A group is stored (space_groups) only once it writes
# to a shared copy, and closed when the call returns; its rows wait until then.

LOCK_WAIT_S = 3.0  # a write to a ``required`` prefix waits this long for its lock
AUTO_ASK_AGAIN_S = 30.0  # an ``auto`` lock refused is asked for again after this
ACTIVITY_EVERY_S = 10.0  # one activity note per unit and writer this often (presence reads 2 minutes)
REFUSED_EVERY_S = 10.0  # one "write refused" event per unit this often


@dataclass
class WriteGroup:
    id: str
    plugin: str
    actor: str = "user"  # user | agent
    ducky: str = ""
    message: str = ""
    # A panel's own data.* call (one write each): its rows go out with the person's other
    # edits instead of one changeset per save.
    panel: bool = False
    stored: bool = False
    closed: bool = False


_GROUPS: contextvars.ContextVar[dict[str, WriteGroup]] = contextvars.ContextVar("plugin_write_groups", default={})
_NOTED: dict[tuple[str, ...], float] = {}  # (account, space, plugin, unit, actor, ducky) → last activity note
_REFUSED: dict[tuple[str, str], float] = {}  # (plugin, unit) → last refused-write event
_NOTE_LOCK = threading.Lock()


def caller(default: str = "user") -> tuple[str, str]:
    """``(actor, ducky)`` of the code running now: ``agent`` with the ducky's name inside an
    agent's run (bound or handed down to a coding agent, or a thread one of its plugin calls
    started), ``agent`` inside a run that limits its plugin tools, else ``default``."""
    try:
        from backend.workspace.identity import resolve_context

        ctx = resolve_context()
    except Exception:  # noqa: BLE001 — attribution never fails a call
        ctx = None
    if ctx is not None:
        return "agent", str(ctx.ducky_name or "")
    try:
        from backend.uefn_plugins.host import _ACTIVE_UEFN_AGENT_IDS

        if _ACTIVE_UEFN_AGENT_IDS.get() is not None:
            return "agent", ""
    except Exception:  # noqa: BLE001
        pass
    # The MCP bridge serves only AI clients (Claude Code, Codex, Cursor...): a write there
    # is an AI's even when it brought no ducky's name, never the person's own. So is an
    # agent host's (it runs only agent turns).
    from backend.process_role import is_app

    if not is_app():
        return "agent", ""
    return default, ""


@contextlib.contextmanager
def write_group(plugin: str, *, actor: str = "user", ducky: str = "", message: str = "", panel: bool = False):
    """Everything ``plugin`` writes inside the block is one group. A group already open for
    the plugin (a panel RPC calling a tool) takes the inner writes too."""
    groups = _GROUPS.get()
    outer = groups.get(plugin)
    if outer is not None and not outer.closed:
        yield outer
        return
    group = WriteGroup(uuid.uuid4().hex, plugin, actor if actor in ("user", "agent") else "user", str(ducky or "")[:80],
                       str(message or "")[:200], panel)
    if group.actor == "agent" and not _inherit_installed:
        _inherit_into_threads()  # the threads this call starts write as the agent too
    token = _GROUPS.set({**groups, plugin: group})
    try:
        with _folder_call(plugin, group):
            yield group
    finally:
        group.closed = True
        try:
            _GROUPS.reset(token)
        except ValueError:  # left from another context: drop just this group
            _GROUPS.set({k: g for k, g in _GROUPS.get().items() if g is not group})
        if group.stored:
            try:
                from backend.store.repos import space_vcs

                space_vcs.group_close(group.id)
            except Exception:  # noqa: BLE001 — the scheduler closes groups left open
                _log.warning("closing write group %s failed", group.id, exc_info=True)


def _folder_call(plugin: str, group: WriteGroup) -> Any:
    """While a tool call or panel RPC of a plugin that keeps declared folders runs, its
    call marker tells a junction re-point in any process to wait for it. A panel's own
    data calls never touch those folders. While a test of the plugin has its folders, a
    call from outside the test is refused: it would read and write the test's copy, which
    goes when the test ends."""
    if group.panel:
        return contextlib.nullcontext()
    testing = False
    try:
        from backend.uefn_plugins import team_folders

        if not team_folders.plugin_folders(plugin):
            return contextlib.nullcontext()
        testing = _boxed(plugin) is None and team_folders.sandboxed(plugin)
        if not testing:
            return team_folders.call_marker(plugin, actor=group.actor, ducky=group.ducky)
    except Exception:  # noqa: BLE001 — a marker never fails the call
        _log.debug("recording the call marker of %s failed", plugin, exc_info=True)
        return contextlib.nullcontext()
    from backend.uefn_plugins.spaces import plugin_label

    raise ValueError(team_folders.TEST_RUNNING.format(plugin=plugin_label(plugin) or plugin))


def open_group(plugin: str) -> WriteGroup | None:
    group = _GROUPS.get().get(plugin)
    return group if group is not None and not group.closed else None


def _clock(at: float) -> str:
    t = time.localtime(at)
    return f"{t.tm_hour % 12 or 12}:{t.tm_min:02d} {'AM' if t.tm_hour < 12 else 'PM'}"


def _unit_label(unit: str) -> str:
    return unit.split(":", 1)[1] if ":" in unit else unit


def _locked_out(plugin: str, unit: str, held: dict[str, Any]) -> LockedOut:
    """The refusal of a write to ``unit`` while ``held`` (someone else's live lock) holds it.
    Panels show it from the answer; anyone else's refusal (an agent's tool call) is told to
    the app, so the plugin's bar and the alerts say who has it."""
    from backend.uefn_plugins.ledger import person

    try:
        from backend.uefn_plugins.vcs_engine import holder_of

        holder, pc = holder_of(str(held.get("account_id") or ""), str(held.get("space_id") or ""), held)
    except Exception:  # noqa: BLE001 — the refusal still names someone
        holder, pc = person(held.get("holder_name"), held.get("holder_user")), ""
    name = str(held.get("name") or _unit_label(unit))
    since = float(held.get("since") or 0.0)
    lock = {"unit": unit, "name": name, "holder": holder, "elsewhere": pc, "since": since}
    group = open_group(plugin)
    if group is None or not group.panel:
        now = time.monotonic()
        with _NOTE_LOCK:
            fresh = now - _REFUSED.get((plugin, unit), float("-inf")) >= REFUSED_EVERY_S
            if fresh:
                _REFUSED[(plugin, unit)] = now
        if fresh:
            try:
                from frontend.ui_web.agent_modes import push_ui_event

                push_ui_event({"type": "plugin_write_refused", "plugin_id": plugin, "unit": unit, "unit_name": name,
                               "holder": holder, "elsewhere": pc, "since": since})
            except Exception:  # noqa: BLE001 — the refusal itself still says it
                _log.debug("telling the app about a refused write failed", exc_info=True)
    when = f" since {_clock(since)}" if since else ""
    if pc:
        # The same account on another PC: still one PC at a time.
        return LockedOut(f"{name} is open on your other PC, {pc}{when}, so it's locked on this PC. Close it there "
                         "(or release it) to change it here. Your change wasn't saved.", lock)
    return LockedOut(f"{name} is locked by {holder}{when}. Your change wasn't saved.", lock)


def _lock_wanted(scope: dict[str, Any], plugin: str, unit: str, name: str, *, wait: bool) -> None:
    """A ``data_locks`` prefix: ask the background sync for the lock (a lock request), and for
    ``required`` wait for it (refused without it)."""
    from backend.store.repos import space_vcs

    account, space = scope["account"], scope["id"]
    if any(lk["unit"] == unit and lk["mine"] for lk in space_vcs.locks(account, space, plugin)):
        return
    req = space_vcs.lock_request_get(account, space, plugin, unit)
    age = time.time() - float(req["created"] or 0.0) if req else float("inf")
    pending = req is not None and req["state"] == "pending" and age < 3 * LOCK_WAIT_S  # else nobody served it
    if not pending and (wait or age >= AUTO_ASK_AGAIN_S):  # an auto lock refused lately isn't asked for on every write
        space_vcs.lock_request_add(account, space, plugin, unit, name)
    if not wait:
        return
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        req = space_vcs.lock_request_get(account, space, plugin, unit)
        state = str(req["state"]) if req else ""
        if state == "granted":
            return
        if state == "refused" or time.monotonic() >= deadline:
            held = space_vcs.foreign_lock(account, space, plugin, unit)
            if held:
                raise _locked_out(plugin, unit, held)
            why = str(req["answer"] or "") if req and state == "refused" else "no answer in time"
            raise ReadOnlyScope(f"{name} needs a lock before it changes, and it couldn't be locked ({why}). "
                                "Your change wasn't saved.")
        time.sleep(0.1)


def _team_write(scope: dict[str, Any], plugin: str, kind: str, key: str, name: str = "") -> str | None:
    """Before a write to a shared copy on the new sync (a team copy): refused while someone
    else holds the item's lock; the item's lock is asked for (:func:`data_lock_policy`,
    named ``name`` so teammates read "Locked by Ana: Untitled island 1"); the write group,
    if one is open, is stored. Returns the group id ('' for none), or None for a copy on
    the older sync (no locks, groups or presence there)."""
    from backend.store.repos import space_vcs

    account, space = scope["account"], scope["id"]
    head = space_vcs.head_get(account, space, plugin)
    if head["engine"] != "v2":
        return None
    if head["state"] == "paused":  # the new sync is switched off on the server, or the space is paused
        why = head["error"] or f"{scope.get('label') or 'This space'} is paused"
        raise ReadOnlyScope(f"{why}. Read-only.")
    unit = f"{kind}:{key}"
    group_pcs = _live_group(scope, plugin)
    held = space_vcs.foreign_lock(account, space, plugin, unit)
    # Live in one group with "data_live": the newest change wins and members never lock
    # each other out (a lock one of them took before joining lapses by itself).
    if held and not (group_pcs is not None and str(held.get("holder_device") or "") in group_pcs):
        raise _locked_out(plugin, unit, held)
    policy = data_lock_policy(plugin, key)
    if policy == "auto" and group_pcs is not None:
        policy = ""
    if policy:
        _lock_wanted(scope, plugin, unit, name or item_label(None, key), wait=policy == "required")
    group = open_group(plugin)
    if group is None or group.panel:
        return ""
    if not group.stored:
        space_vcs.group_open(group.id, plugin, actor=group.actor, ducky=group.ducky, message=group.message)
        group.stored = True
    return group.id


def _note_write(scope: dict[str, Any], plugin: str, kind: str, key: str) -> None:
    """A shared item changed here: presence says who is editing it (at most one note per
    unit and writer every few seconds)."""
    group = open_group(plugin)
    actor, ducky = (group.actor, group.ducky) if group is not None else caller()
    unit = f"{kind}:{key}"
    seen = (scope["account"], scope["id"], plugin, unit, actor, ducky)
    now = time.monotonic()
    with _NOTE_LOCK:
        if now - _NOTED.get(seen, float("-inf")) < ACTIVITY_EVERY_S:
            return
        if len(_NOTED) > 2000:
            _NOTED.clear()
        _NOTED[seen] = now
    try:
        from backend.store.repos import space_vcs

        space_vcs.activity_note(scope["account"], scope["id"], plugin, unit, actor=actor, ducky=ducky)
    except Exception:  # noqa: BLE001 — the write itself landed
        _log.debug("noting activity on %s failed", unit, exc_info=True)


# --------------------------------------------------------------------------- change callbacks
#
# ``api.data.on_change(callback)``: a pull that changed a plugin's data calls
# ``callback(keys)`` (doc keys and file paths) in every process that loaded the plugin.
# The app process (where pulls run) calls it right away; any other process (the MCP
# bridge) hears it before the plugin's next tool call, from the plugin's epoch row.

_CHANGE_LISTENERS: dict[str, list[Any]] = {}
_CHANGE_SEEN: dict[str, int] = {}  # plugin → epoch delivered in this process
_CHANGE_LOCK = threading.Lock()


def add_change_listener(plugin: str, callback: Any) -> None:
    if not callable(callback):
        raise TypeError("on_change needs a function: callback(keys)")
    from backend.store.repos import space_vcs

    epoch = space_vcs.change_log(plugin)["epoch"]
    with _CHANGE_LOCK:
        _CHANGE_LISTENERS.setdefault(plugin, []).append(callback)
        _CHANGE_SEEN.setdefault(plugin, epoch)  # only changes from now on


def forget_change_listeners(plugin: str) -> None:
    """The plugin unloaded: its callbacks belong to modules that are gone."""
    with _CHANGE_LOCK:
        _CHANGE_LISTENERS.pop(plugin, None)
        _CHANGE_SEEN.pop(plugin, None)


def _call_listeners(plugin: str, listeners: list[Any], keys: list[str]) -> None:
    import logging

    for callback in listeners:
        try:
            callback(list(keys))
        except Exception:  # noqa: BLE001 — a plugin's callback never breaks a pull or a tool call
            logging.getLogger(__name__).warning("%s: data on_change callback failed", plugin, exc_info=True)


def deliver_data_changes(plugin: str) -> int:
    """What pulls changed since this process last looked, to the plugin's callbacks: after a
    pull here, and before the plugin's tool calls for pulls in another process. Every entry
    of the plugin's change log since then, so a pull elsewhere is never jumped over.
    Returns how many keys were delivered."""
    with _CHANGE_LOCK:
        listeners = list(_CHANGE_LISTENERS.get(plugin, ()))
        seen = _CHANGE_SEEN.get(plugin)
    if not listeners:
        return 0
    from backend.store.repos import space_vcs

    log = space_vcs.change_log(plugin)
    with _CHANGE_LOCK:
        seen = _CHANGE_SEEN.get(plugin, seen)
        if seen is None or log["epoch"] <= seen:
            if seen is None:
                _CHANGE_SEEN[plugin] = log["epoch"]
            return 0
        _CHANGE_SEEN[plugin] = log["epoch"]
    entries = [c for c in log["changes"] if int(c[0]) > seen]
    if seen < log["trimmed"] or any(c[1] is None for c in entries):
        data = PluginData(plugin)  # too much changed to list: every key
        keys = data.keys() + [f["path"] for f in data.files()]
    else:
        keys = sorted({k for c in entries for k in c[1]})
    if keys:
        _call_listeners(plugin, listeners, keys)
    return len(keys)


class PluginData:
    """One plugin's docs and files in the active scope (``api.data`` for backends,
    ``data.*`` / ``files.*`` for panels). Each call resolves the scope afresh, so a
    project or account switch is picked up without a plugin reload."""

    def __init__(self, plugin_id: str, *, personal: bool = False) -> None:
        _need(valid_plugin_id(plugin_id), "plugin id")
        self.plugin_id = plugin_id
        self._personal = personal

    # -- scope

    def personal(self) -> "PluginData":
        """The same plugin pinned to the account's Personal scope (one-time imports of
        old local data land here, whatever the open project is linked to)."""
        return PluginData(self.plugin_id, personal=True)

    def _scope(self) -> dict[str, Any]:
        if self._personal:
            return _personal_for(self.plugin_id)
        s = active_scope(self.plugin_id)
        if s["state"] == "waiting":
            # First use of a shared scope on this PC: pull before the plugin sees it (bounded).
            from backend.uefn_plugins.spaces import provider

            if provider().first_pull(s["account"], s["id"], FIRST_PULL_WAIT_S):
                s = active_scope(self.plugin_id)
        return s

    def scope(self) -> dict[str, Any]:
        return scope_view(self._scope())

    def _local(self, key: str) -> bool:
        """``key`` is one of this PC's own items (manifest ``data_local``): it lives in Local."""
        return not self._personal and is_local_key(self.plugin_id, key)

    def _scope_for(self, key: str) -> dict[str, Any]:
        return _personal_for(self.plugin_id) if self._local(key) else self._scope()

    def _rows(self, kind: str, prefix: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """``(scope, row)`` of every live row under ``prefix``, by key: this PC's own items
        (``data_local``) from Local, the rest from the copy the plugin shows (a team copy's
        own items under a ``data_local`` prefix are never read)."""
        from backend.store.repos import plugin_data as repo

        s = self._scope()
        out = [] if _closed(s) else [(s, r) for r in repo.rows(s["account"], s["id"], self.plugin_id, kind, prefix)]
        local = () if self._personal else local_prefixes(self.plugin_id)
        mine = _personal_for(self.plugin_id) if local else s
        if not local or (mine["account"], mine["id"]) == (s["account"], s["id"]):
            return out
        out = [(sc, r) for sc, r in out if not any(r["key"].startswith(p) for p in local)]
        own: dict[str, dict[str, Any]] = {}
        for p in local:
            narrower = p if p.startswith(prefix) else prefix if prefix.startswith(p) else None
            if narrower is not None:
                own.update((r["key"], r) for r in repo.rows(mine["account"], mine["id"], self.plugin_id, kind, narrower))
        return sorted([*out, *((mine, r) for r in own.values())], key=lambda pair: pair[1]["key"])

    def on_change(self, callback: Any) -> None:
        """``callback(keys)`` after a sync brought changes to this plugin's data (doc keys
        and file paths). Register once in ``register(api)``; drop caches of those keys."""
        add_change_listener(self.plugin_id, callback)

    def _writable(self, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        s = scope or self._scope()
        if s["state"] == "locked":
            raise ReadOnlyScope("Waiting for this account's data key. Plugin data opens once you're online.")
        if s["state"] == "waiting":
            raise ReadOnlyScope(f"Waiting for {s['label']}'s data. Read-only until it arrives.")
        if s["state"] == "lost":
            raise ReadOnlyScope(f"You no longer have access to {s['label']}. Its data on this PC is locked.")
        if s["state"] == "unavailable":
            reason = f" ({s['reason']})" if s.get("reason") else ""
            raise ReadOnlyScope(f"{s['label']} isn't available right now{reason}. Read-only until it's back.")
        if s["state"] == "reset":
            raise ReadOnlyScope(f"{s['label']}'s data was reset on the server. Read-only until you choose what to "
                                "do with your copy.")
        if s["readOnly"]:
            raise ReadOnlyScope(f"{s['label']} is paused. Read-only.")
        return s

    # -- docs (sealed for the account at rest, plan §13; sha256/size are of the plaintext)

    def get(self, key: str, default: Any = None, *, sensitive: bool = False) -> Any:
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins.data_crypto import Locked, open_text

        _need(valid_doc_key(key), "doc key")
        s = _personal_for(self.plugin_id) if sensitive else self._scope_for(key)
        if _closed(s):
            return default
        row = repo.get(s["account"], s["id"], self.plugin_id, "doc", key)
        if not row or row["deleted"] or row["value"] is None:
            return default
        try:
            return json.loads(open_text(row["value"], s["account"]))
        except Locked:
            return default

    def put(self, key: str, value: Any, *, sensitive: bool = False) -> dict[str, Any]:
        """Write one doc. ``sensitive=True`` keeps it in Personal, never synced."""
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins.data_crypto import seal_text

        _need(valid_doc_key(key), "doc key")
        s = self._writable(_personal_for(self.plugin_id) if sensitive or self._local(key) else None)
        raw = encode_doc(value)
        shared_max = DOC_MAX_BYTES - SHARED_SEAL_OVERHEAD
        if s["kind"] == "shared" and len(raw) > shared_max:
            raise ValueError(f"doc {key!r} is too big to share (over {shared_max:,} bytes); split it into one doc per entity")
        if len(raw) > DOC_MAX_BYTES:
            raise ValueError(f"doc {key!r} is over 1 MB; split it into one doc per entity")
        sha = _sha(raw)
        old = repo.get(s["account"], s["id"], self.plugin_id, "doc", key)
        if old and not old["deleted"] and old["sha256"] == sha:
            return {"ok": True, "key": key, "changed": False}
        shared = s["kind"] == "shared" and not sensitive
        group = _team_write(s, self.plugin_id, "doc", key, item_label(value, key)) if shared else None
        repo.put(s["account"], s["id"], self.plugin_id, "doc", key, value=seal_text(raw.decode("utf-8"), s["account"]),
                 size=len(raw), sha256=sha, dirty=shared, sensitive=sensitive, group_id=group or "")
        if group is not None:
            _note_write(s, self.plugin_id, "doc", key)
        return {"ok": True, "key": key, "changed": True}

    def keys(self, prefix: str = "") -> list[str]:
        return [r["key"] for _, r in self._rows("doc", prefix) if not r["sensitive"]]

    def items(self, prefix: str = "") -> dict[str, Any]:
        """All docs under ``prefix`` in one read: ``{key: value}``."""
        from backend.uefn_plugins.data_crypto import Locked, open_text

        out: dict[str, Any] = {}
        try:
            for s, r in self._rows("doc", prefix):
                if not r["sensitive"] and r["value"] is not None:
                    out[r["key"]] = json.loads(open_text(r["value"], s["account"]))
        except Locked:
            return {}
        return out

    def delete(self, key: str) -> bool:
        from backend.store.repos import plugin_data as repo

        _need(valid_doc_key(key), "doc key")
        s = self._writable(_personal_for(self.plugin_id) if self._local(key) else None)
        old = repo.get(s["account"], s["id"], self.plugin_id, "doc", key)
        if not old or old["deleted"]:
            return False
        shared = s["kind"] == "shared"
        group = _team_write(s, self.plugin_id, "doc", key) if shared else None
        # Always a tombstone in a shared copy: the server may hold the doc even at rev 0
        # (a push in flight, a lost answer), and a delete of an item it never had is harmless.
        repo.remove(s["account"], s["id"], self.plugin_id, "doc", key, tombstone=shared, group_id=group or "")
        if group is not None:
            _note_write(s, self.plugin_id, "doc", key)
        return True

    # -- files (assets)

    def put_file(self, path: str, data: bytes) -> dict[str, Any]:
        from backend.store.repos import plugin_data as repo
        from backend.uefn_plugins.data_crypto import seal_bytes

        s = self._writable(_personal_for(self.plugin_id) if self._local(path) else None)
        target = asset_file(s, self.plugin_id, path)
        shared = s["kind"] == "shared"
        shared_max = ASSET_MAX_BYTES - SHARED_SEAL_OVERHEAD
        if shared and len(data) > shared_max:
            raise ValueError(f"file {path!r} is too big to share (over {shared_max:,} bytes)")
        if len(data) > ASSET_MAX_BYTES:
            raise ValueError(f"file {path!r} is over 100 MB")
        sha = _sha(data)
        old = repo.get(s["account"], s["id"], self.plugin_id, "asset", path)
        if old and not old["deleted"] and old["sha256"] == sha and target.is_file():
            return {"ok": True, "path": path, "changed": False}
        group = _team_write(s, self.plugin_id, "asset", path) if shared else None
        _write_asset(s, self.plugin_id, path, seal_bytes(data, s["account"]), size=len(data), sha256=sha, dirty=shared,
                     group_id=group or "")
        if group is not None:
            _note_write(s, self.plugin_id, "asset", path)
        return {"ok": True, "path": path, "changed": True, "size": len(data)}

    def has_file(self, path: str) -> bool:
        from backend.store.repos import plugin_data as repo

        s = self._scope_for(path)
        if _closed(s):
            return False
        row = repo.get(s["account"], s["id"], self.plugin_id, "asset", path)
        return bool(row and not row["deleted"] and asset_file(s, self.plugin_id, path).is_file())

    def get_file(self, path: str) -> bytes | None:
        """A stored file's bytes (sealed on disk, so there is no path to hand out)."""
        from backend.uefn_plugins.data_crypto import Locked, open_bytes

        s = self._scope_for(path)
        if not self.has_file(path):
            return None
        try:
            return open_bytes(asset_file(s, self.plugin_id, path).read_bytes(), s["account"])
        except Locked:
            return None

    def files(self, prefix: str = "") -> list[dict[str, Any]]:
        return [{"path": r["key"], "size": r["size"], "sha256": r["sha256"]} for _, r in self._rows("asset", prefix)]

    def delete_file(self, path: str) -> bool:
        from backend.store.repos import plugin_data as repo

        s = self._writable(_personal_for(self.plugin_id) if self._local(path) else None)
        target = asset_file(s, self.plugin_id, path)
        old = repo.get(s["account"], s["id"], self.plugin_id, "asset", path)
        if not old or old["deleted"]:
            return False
        shared = s["kind"] == "shared"
        group = _team_write(s, self.plugin_id, "asset", path) if shared else None
        # The row first, then the file, in one transaction: never a live row without its file.
        repo.remove(s["account"], s["id"], self.plugin_id, "asset", path, tombstone=shared, target_path=target,
                    group_id=group or "")
        if group is not None:
            _note_write(s, self.plugin_id, "asset", path)
        return True


def panel_call(plugin_id: str, op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Panel bridge entry (``data.*`` / ``files.*`` / ``scope.get``): one dispatcher, JSON in and
    out. A write is the person's own (a user write group)."""
    with write_group(plugin_id, actor="user", message=f"Panel {op}", panel=True):
        return _panel_op(plugin_id, op, params)


def _panel_op(plugin_id: str, op: str, params: dict[str, Any] | None) -> dict[str, Any]:
    p = params or {}
    data = PluginData(plugin_id)
    if op == "scope.get":
        return {"ok": True, "scope": data.scope()}
    if op == "data.get":
        return {"ok": True, "key": p.get("key"), "value": data.get(str(p.get("key") or ""))}
    if op == "data.put":
        return data.put(str(p.get("key") or ""), p.get("value"))
    if op == "data.list":
        prefix = str(p.get("prefix") or "")
        return {"ok": True, "items": data.items(prefix)} if p.get("values") else {"ok": True, "keys": data.keys(prefix)}
    if op == "data.delete":
        return {"ok": True, "deleted": data.delete(str(p.get("key") or ""))}
    if op == "files.put":
        raw = base64.b64decode(str(p.get("b64") or ""), validate=True)
        return data.put_file(str(p.get("path") or ""), raw)
    if op == "files.get":
        blob = data.get_file(str(p.get("path") or ""))
        return {"ok": blob is not None, "b64": base64.b64encode(blob).decode("ascii") if blob is not None else None}
    if op == "files.list":
        return {"ok": True, "files": data.files(str(p.get("prefix") or ""))}
    if op == "files.delete":
        return {"ok": True, "deleted": data.delete_file(str(p.get("path") or ""))}
    raise ValueError(f"unknown data op: {op}")
