"""Shared spaces: copies of plugin data and workflows that live outside Local.

Core keeps every copy on this PC (:mod:`backend.uefn_plugins.scopes` stores a shared
space's rows and files exactly like Local's) and does the bookkeeping a sync needs:
dirty flags, tombstones, revisions, a per-space sync row (label, state, cursor) and a
per-space meta row. A space's state comes from its sync row, which the provider keeps.
Core does not know which spaces exist, who may use them, or how a copy reaches anyone
else.
A provider plugin answers those questions through :class:`Provider`. Without one the
pickers offer only Local and nothing syncs; copies already on this PC stay as they are
(a provider plugin that is updating comes back to them).

One provider at a time. It must come from a Store-signed install (a local build only
when ``DUCKY_DEV_SPACES=1``), because it reads and moves every plugin's shared data.

A provider can also hand core a ledger per plugin (:meth:`Provider.ledger`): core's
version engine (:mod:`backend.uefn_plugins.vcs_engine`) then syncs that plugin's copy as
changesets, with locks, presence, conflicts and history, and the provider's own
:meth:`Provider.sync` keeps the rest. The provider tells core what it heard about a space
(:func:`remote_event`) and asks whether the space is in active use (:func:`space_active`).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Mapping

from backend.uefn_plugins.ledger import person

_log = logging.getLogger(__name__)

# Generic space states. A provider reports one; core decides read-only and closed.
# ``unavailable``: the provider can't use the space now (the sync row's error says why);
# ``reset``: its data was reset on the server. ``no_provider`` (core's own) stays writable.
STATES_READ_ONLY = frozenset({"paused", "waiting", "locked", "lost", "unavailable", "reset"})
STATE_CLOSED = "lost"  # access gone: the copy here reads as empty until it comes back
NO_PROVIDER_ERROR = "Not connected: Ducky Account isn't running"
NO_LEDGER_ERROR = "This copy still syncs the older way: locks, history and restore come once it moves to the new sync."
WORKFLOW_DOCS = "ducky.automations"  # the plugin id workflows are kept under (owned.DOC_PLUGIN)
# What a provider hears about a space (:func:`remote_event`): a new head of a plugin, a
# lock taken or released, someone's presence, a request to release a lock of this PC, the
# space's data reset, an import moving on.
REMOTE_EVENTS = frozenset({"head", "lock", "presence", "ask", "epoch", "import"})


class Provider:
    """What a shared-space service adds. Every method has a Local-only default, so a
    provider overrides only what it offers."""

    #: How names read: ``f"{kind_label} {label}"`` in tool results ("space Alpha").
    kind_label = "space"
    #: What a paused (read-only) space's line says in lists.
    paused_label = "Paused"

    # -- plugin data
    def enabled(self) -> bool:
        """Show the space picker and sync line at all (the account can use spaces)."""
        return False

    def choices(self, purpose: str) -> list[dict[str, Any]]:
        """Spaces the account can pick, besides Local. ``purpose``: ``data`` (where a
        plugin's data lives) or ``workflows`` (workflow owners). Each: id, label, and
        any display extras (members, slug)."""
        return []

    def choices_note(self, purpose: str) -> str:
        """What a picker says when there is nothing to pick besides Local."""
        return ""

    def status(self, account: str, space_id: str, plugin: str) -> dict[str, Any]:
        """Extra fields for the scope bar of ``plugin`` while it shows ``space_id``."""
        return {}

    def settle_lost(self, account: str, space_id: str) -> str:
        """A read found the space lost: return its state now (``unavailable`` after the
        provider dropped the copy here, e.g. once access has been gone long enough)."""
        return "lost"

    def linked(self, account: str, plugin: str, space_id: str) -> None:
        """A plugin's data was just pointed at ``space_id`` (record labels, retry access)."""

    def first_pull(self, account: str, space_id: str, timeout: float) -> bool:
        """Bring a space this PC never pulled before a plugin reads it. True once pulled."""
        return False

    def sync(self, account: str, space_id: str, *, force: bool = False) -> dict[str, Any]:
        """One round for the space: ``{state, changed, error}`` (``skipped`` when too soon)."""
        return {"state": "ok", "changed": [], "error": "", "skipped": True}

    def ledger(self, account: str, space_id: str, plugin: str) -> Any:
        """The remote side of ``plugin``'s copy in the space, as a
        :class:`backend.uefn_plugins.ledger.LedgerBackend` (core's version engine syncs it:
        changesets, locks, presence, history), or None to keep the plugin on :meth:`sync`.
        Asked for every plugin of a space every few seconds, in the app and the MCP bridge:
        answer quickly from what is already known (cache it) and never raise. A
        :meth:`sync` round of the space leaves plugins with a ledger out. Core moves the
        plugin's copy over itself on its first round (a full listing with each imported
        item's ``meta["v1rev"]``); the provider never writes core's tables for it. Once a
        plugin moved, keep returning its ledger, also while the new sync is switched off on
        the server (``Heads.v2`` false): core then keeps it read-only, "Team sync paused"."""
        return None

    def open_web(self, *, space_id: str = "", plugin: str = "", page: str = "") -> dict[str, Any]:
        """Open the space (or its ``page``, e.g. ``workflows`` / ``plugins``) on the web."""
        return {"ok": False, "error": "Nothing to open."}

    def on_login_change(self, keep_account: str) -> None:
        """The signed-in account changed; drop what belonged to other accounts."""

    def reset_for_tests(self) -> None:
        """Forget in-memory state between tests."""

    # -- workflows
    def workflow_spaces(self, account: str) -> list[str]:
        """Spaces whose workflows this account sees (in display order)."""
        return []

    def workflow_edit_refusal(self, account: str, space_id: str, label: str) -> str:
        """Why this account can't change the space's workflows from this PC ("" when it can)."""
        return ""

    def refresh_workflow_spaces(self) -> None:
        """Ask the service which spaces the account has (when the Workflows view opens)."""

    # -- private Store items
    def catalog_item(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Audience fields of one Store catalog item (``audience_id``, ``audience_label``,
        ``mine``). Empty: the item is shown like any public one."""
        return {}

    def catalog_extras(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Catalog-level fields: ``audiences`` (bool: show the Shared category),
        ``audienceNotes`` (per-audience notes), ``audienceCategoryLabel``."""
        return {}

    # -- live editing (backend.uefn_plugins.live; peers through rtc_bridge)
    def live_supported(self) -> bool:
        """Teammates can go live together (no Live button without it)."""
        return False

    def live_join(self, account: str, space_id: str, plugin: str, room: str) -> None:
        """This PC is live in ``room`` of ``plugin`` (presence already says so): connect to
        the teammates live there and deliver what they send with :func:`live.deliver`."""

    def live_leave(self, account: str, space_id: str, plugin: str) -> None:
        """This PC left ``plugin``'s room."""

    def live_send(self, account: str, space_id: str, plugin: str, topic: str, data: Any, *,
                  to: str = "", fast: bool = False) -> int:
        """``data`` (JSON) to the room, or to one Ducky (``to`` = its device id); ``fast``:
        pointers, may drop. Returns how many Duckies it went to."""
        return 0

    def live_state(self, account: str, space_id: str, plugin: str) -> dict[str, Any]:
        """``{state, peers}``: each peer's ``name``, ``user``, ``device``, ``label`` and
        ``path`` (``direct``, ``forwarded`` or ``none``)."""
        return {"state": "off", "peers": []}


_NULL = Provider()
_LOCK = threading.Lock()
_ACTIVE: dict[str, Any] = {"plugin": "", "provider": _NULL}


def provider() -> Provider:
    """The registered provider, or a Local-only stand-in."""
    return _ACTIVE["provider"]


def provider_plugin() -> str:
    return _ACTIVE["plugin"]


def has_provider() -> bool:
    return _ACTIVE["provider"] is not _NULL


def _trusted(plugin_id: str) -> bool:
    if os.environ.get("DUCKY_DEV_SPACES") == "1":
        return True
    try:
        from backend.uefn_plugins import signing
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(plugin_id) or {}
        if str(manifest.get("source") or "") != "store":
            return False
        return bool(signing.installed_record_ok(plugin_id))
    except Exception:  # noqa: BLE001 — an unreadable record is not a signed one
        _log.debug("signed-record check failed for %s", plugin_id, exc_info=True)
        return False


def register(plugin_id: str, impl: Provider) -> None:
    """Make ``impl`` the shared-space provider (``api.provide_spaces``)."""
    if not isinstance(impl, Provider):
        raise TypeError("a spaces provider subclasses backend.uefn_plugins.spaces.Provider")
    if not _trusted(plugin_id):
        raise PermissionError("Only a Store-signed plugin can provide shared spaces.")
    with _LOCK:
        current = _ACTIVE["plugin"]
        if current and current != plugin_id:
            raise PermissionError(f"Shared spaces are already provided by {current}.")
        _ACTIVE.update(plugin=plugin_id, provider=impl)
    _log.info("shared spaces provided by %s", plugin_id)


def unregister(plugin_id: str) -> None:
    """The provider's plugin unloaded: back to Local only."""
    with _LOCK:
        was = _ACTIVE["plugin"] == plugin_id
        if was:
            _ACTIVE.update(plugin="", provider=_NULL)
    if was:
        try:
            from backend.uefn_plugins import live

            live.leave_all()
        except Exception:  # noqa: BLE001 — nothing is live without a provider anyway
            _log.debug("leaving live rooms failed", exc_info=True)


def rtc_bridge(plugin_id: str) -> Any:
    """The WebRTC bridge (:mod:`backend.uefn_plugins.rtc`) for the registered spaces
    provider. Peer connections are the provider's alone: any other plugin is refused."""
    if not plugin_id or plugin_id != _ACTIVE["plugin"]:
        raise PermissionError("rtc is for the spaces provider")
    from backend.uefn_plugins import rtc

    return rtc.bridge()


def reset_for_tests() -> None:
    try:
        provider().reset_for_tests()
    finally:
        with _LOCK:
            _ACTIVE.update(plugin="", provider=_NULL)


def install_for_tests(impl: Provider, plugin_id: str = "test-spaces") -> None:
    """Tests: register ``impl`` without the signed-install check."""
    with _LOCK:
        _ACTIVE.update(plugin=plugin_id, provider=impl)


# --------------------------------------------------------------------------- host glue


def plugin_label(plugin_id: str) -> str:
    """The plugin's display name, from its manifest. Empty when it has none."""
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(plugin_id) or {}
        label = str(manifest.get("label") or "").strip()
        if label and len(label) <= 80 and all(ord(c) >= 32 for c in label):
            return label
    except Exception:
        return ""
    return ""


_BAD_LEDGER: set[str] = set()  # providers already logged for answering with something else


def ledger_for(account: str, space: str, plugin: str) -> Any:
    """``plugin``'s ledger backend in the space (:meth:`Provider.ledger`), or None (no
    provider, the older sync, or a provider that failed to answer)."""
    impl = provider()
    try:
        got = impl.ledger(account, space, plugin)
    except Exception:  # noqa: BLE001 — a failing provider keeps the older sync
        _log.debug("ledger of %s in %s failed", plugin, space, exc_info=True)
        return None
    if got is None:
        return None
    from backend.uefn_plugins.ledger import LedgerBackend

    if not isinstance(got, LedgerBackend):
        if provider_plugin() not in _BAD_LEDGER:
            _BAD_LEDGER.add(provider_plugin())
            _log.warning("%s answered ledger() with %s, not a LedgerBackend", provider_plugin(), type(got).__name__)
        return None
    return got


def ledgers(account: str, space: str, plugins: Any) -> dict[str, Any]:
    """``{plugin: ledger backend}`` of the ``plugins`` that have one in the space."""
    out: dict[str, Any] = {}
    for plugin in sorted(set(plugins)):
        got = ledger_for(account, space, plugin)
        if got is not None:
            out[plugin] = got
    return out


def remote_event(account: str, space: str, kind: str, plugin: str | None = None,
                 data: Mapping[str, Any] | None = None) -> None:
    """The provider heard something about a space (``kind``, one of :data:`REMOTE_EVENTS`):
    the background sync looks at the space again soon (heads; a ``head`` of ``plugin`` also
    pulls it). ``ask``: someone asks this PC to release a lock (``data``: ``unit``, and
    ``user_name`` or ``user`` of who asks); the person is told. Only a hint: a missed event
    only delays a pull. Never raises."""
    try:
        if kind not in REMOTE_EVENTS:
            return
        if kind == "ask":
            _asked(account, space, str(plugin or ""), dict(data or {}))
            return
        from backend.uefn_plugins import sync_scheduler

        sync_scheduler.wake(account, space, plugin if kind == "head" and plugin else None)
    except Exception:  # noqa: BLE001 — a hint never breaks the provider's waiter
        _log.debug("remote event %s for %s failed", kind, space, exc_info=True)


def _asked(account: str, space: str, plugin: str, data: dict[str, Any]) -> None:
    """Someone asks this PC to release a lock it holds: an alert, and the plugin's bar hears
    it (it offers Release)."""
    from backend.store.repos import space_vcs

    unit = str(data.get("unit") or "")
    mine = next((lk for lk in space_vcs.locks(account, space, plugin or None, live=False)
                 if lk["unit"] == unit and lk["mine"]), None)
    if mine is None:
        return
    plugin = plugin or str(mine["plugin_id"])
    who = person(data.get("user_name"), data.get("user"), fallback="A teammate")
    name = str(mine["name"] or unit.split(":", 1)[-1])
    label = plugin_label(plugin) or plugin
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "plugin_lock_asked", "plugin_id": plugin, "unit": unit, "unit_name": name,
                       "holder": who})
    except Exception:  # noqa: BLE001 — the alert below still tells the person
        _log.debug("telling the panel about an ask failed", exc_info=True)
    from frontend.notifications import alert

    alert(label, "A teammate asks you to release a lock.", level="ask", detail=f"{who} asks you to release {name}.")


def space_active(account: str, space: str) -> bool:
    """Whether the space is in use on this PC right now: a linked plugin's panel or the
    Workflows view on screen, a change made here in the last 5 minutes, a lock held here,
    or changes still to send. The provider keeps its live channel open only while true."""
    from backend.uefn_plugins import sync_scheduler

    try:
        return sync_scheduler.space_active(account, space)
    except Exception:  # noqa: BLE001 — unsure: not active (heads still come every 15 minutes)
        _log.debug("space_active failed for %s", space, exc_info=True)
        return False


def sync_active(plugin: str, *, force: bool = False, on_done: Any = None) -> dict[str, Any]:
    """Start a round for the shared space ``plugin``'s data lives in, on a worker thread
    (no-op for Local). Without a provider nothing can sync, and the answer says so."""
    from backend.uefn_plugins import scopes

    scope = scopes.active_scope(plugin)
    if scope["kind"] != "shared":
        return {"ok": True, "started": False}
    return sync_space(scope["account"], scope["id"], force=force, on_done=on_done)


def sync_space(account: str, space: str, *, force: bool = False, on_done: Any = None) -> dict[str, Any]:
    """:func:`sync_active` for a space by id. The round holds the space's round lock, so
    it never overlaps one of the same space in another process (the MCP bridge)."""
    if not has_provider():
        return {"ok": False, "started": False, "error": NO_PROVIDER_ERROR}

    def _work() -> None:
        from backend.uefn_plugins.sync_scheduler import run_round

        out = run_round(account, space, force=force)
        if on_done and (out.get("changed") or not out.get("skipped")):
            on_done(out)

    threading.Thread(target=_work, name="space-data-sync", daemon=True).start()
    return {"ok": True, "started": True}


def _round_failed(scope: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """A provider's round raised. The space shows the error (the bar says "Not synced" and
    why) and the provider plugin's errors get it with the traceback; before, the thread
    died silently and the bar kept saying when the last round worked."""
    import traceback

    from backend.store.repos import plugin_data as repo

    msg = f"Sync failed: {type(exc).__name__}: {exc}"
    _log.warning("sync of space %s failed", scope["id"], exc_info=True)
    try:
        repo.sync_put(scope["account"], scope["id"], state="error", error=msg[:500])
        from backend.tools.panel.panel_plugin_check import record

        record("plugin_sync_error", provider_plugin(), msg, stack=traceback.format_exc()[-4000:])
    except Exception:  # noqa: BLE001 — reporting must not raise from the sync thread
        _log.debug("recording the sync failure failed", exc_info=True)
    return {"state": "error", "changed": [], "error": msg}


def space_contents(account: str, space: str) -> list[str]:
    """What this PC keeps of a space, by name: each plugin's label, sorted, then
    ``Workflows`` when it holds the space's workflows."""
    from backend.store.repos import plugin_data as repo

    plugins = repo.scope_plugin_ids(account, space)
    names = {plugin_label(plugin) or plugin for plugin in plugins if plugin != WORKFLOW_DOCS}
    return sorted(names, key=str.casefold) + (["Workflows"] if WORKFLOW_DOCS in plugins else [])


def settle_reset(space: str, action: str) -> dict[str, Any]:
    """The person's choice for a space whose data was reset on the server: ``upload``
    (Upload my copy: this PC's copy goes up again as new) or ``clear`` (Clear my copy: it
    goes, rows and files, and the space's data comes down instead). Either one is for the
    space's whole copy here, every plugin's and its workflows, because the provider waits
    for all of it; folders plugins keep included (their copies stay in place, emptied on
    Clear my copy, so their junctions never dangle). ``plugins``: the plugin ids whose copy
    it was."""
    from backend.store.repos import plugin_data as repo
    from backend.store.repos import workflows
    from backend.uefn_plugins import scopes

    if action not in ("upload", "clear"):
        raise ValueError("Choose Upload my copy or Clear my copy.")
    account = scopes.account_id()
    if account == scopes.LOCAL or not scopes.valid_space_id(space):
        raise ValueError("invalid space id")
    sync = repo.sync_get(account, space)
    if sync["state"] != "reset":
        return {"ok": False, "error": f"{sync['label'] or 'This space'}'s data wasn't reset on the server."}
    plugins = repo.scope_plugin_ids(account, space)
    space_workflows = [r["key"] for r in repo.rows(account, space, WORKFLOW_DOCS, "doc")]
    if action == "upload":
        from backend.uefn_plugins import vcs_engine

        vcs_engine.conflicts_into_rows(account, space)  # an unresolved conflict's mine is part of my copy
    count = repo.settle_reset(account, space, keep=action == "upload")
    if action == "upload":
        return {"ok": True, "plugins": plugins, "queued": count}
    # The copy's files and what this PC derived from it go too; its links and sync row stay.
    workflows.runtime_delete(account, space_workflows)
    try:
        from backend.store.repos import search as search_repo

        search_repo.drop_scope_records(account, space)
    except Exception:  # noqa: BLE001 — a search error never blocks the clear
        _log.debug("dropping search records of %s failed", space, exc_info=True)
    from backend.uefn_plugins import team_folders

    team_folders.clear_space(account, space)
    return {"ok": True, "plugins": plugins, "cleared": count}


def scope_status(plugin: str) -> dict[str, Any]:
    """Where ``plugin``'s data lives: the host scope bar and the Plugins page. ``visible``
    is false while the account has no shared spaces to offer, and always for a plugin
    that never shares its data (``"data_sharing": false``). ``shareable`` is false for a
    plugin that keeps its own files (:func:`scopes.shareable`): no space is offered and
    the bar says Local only. ``local`` / ``current`` are the Local and the shown copy's
    counts and bytes.

    For a shared copy: ``pending`` is this plugin's changes still to send; ``refused``,
    ``blocked`` and ``conflict`` count its changes that aren't going out, listed in
    ``items`` (kind, key, state, reason, attempts, deleted). ``resetContents`` (state
    ``reset``) names everything Upload my copy / Clear my copy act on. The team fields
    (:func:`team_fields`): ``people``, ``locks``, ``changes``, ``conflicts``, ``mode``,
    ``head``, ``nextTry``, ``importing``, and for a lost space ``unsent`` / ``unsentChosen``."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes
    from frontend.duckyos_account import get_status

    scope = scopes.active_scope(plugin)
    account = scope["account"]
    shared = scope["kind"] == "shared"
    out: dict[str, Any] = {
        "ok": True,
        # No bar at all for a plugin that can't share (it keeps its own files, or says
        # data_sharing: false): there is nothing to pick.
        "visible": shared or (scopes.shareable(plugin) and provider().enabled()),
        "shareable": shared or scopes.shareable(plugin),
        "kindLabel": provider().kind_label,
        "scope": scopes.scope_view(scope),
        "pluginLabel": plugin_label(plugin) or plugin,
        "email": str(get_status().get("email") or ""),
        "canChange": account != scopes.LOCAL,
        "state": scope["state"],
        "local": repo.totals(account, scopes.PERSONAL, plugin),
        "current": repo.totals(account, scope["id"], plugin),
    }
    try:
        out.update(_folder_fields(plugin))
    except Exception:  # noqa: BLE001 — the bar still shows the rest
        _log.debug("folder fields failed for %s", plugin, exc_info=True)
    if shared:
        st = repo.sync_get(account, scope["id"])
        held = repo.state_counts(account, scope["id"], plugin)
        out.update(
            members=int(st["members"]),
            error=NO_PROVIDER_ERROR if scope["state"] == "no_provider" else st["error"],
            syncedAt=float(st["synced_at"]) or None,
            pending=repo.count_dirty(account, scope["id"], plugin),
            refused=held["refused"],
            blocked=held["blocked"],
            conflict=held["conflict"],
            items=[
                {"kind": r["kind"], "key": r["key"], "state": r["sync_state"], "reason": r["sync_error"],
                 "attempts": int(r["attempts"]), "deleted": bool(r["deleted"])}
                for r in repo.state_items(account, scope["id"], plugin)
            ],
            usage=st["usage"] or {},
        )
        if scope["state"] == "reset":
            out["resetContents"] = space_contents(account, scope["id"])
        try:
            out.update(team_fields(account, scope["id"], plugin, state=scope["state"]))
        except Exception:  # noqa: BLE001 — the bar still shows the rest
            _log.debug("team fields failed for %s", plugin, exc_info=True)
        try:
            out.update(provider().status(account, scope["id"], plugin) or {})
        except Exception:  # noqa: BLE001 — the bar still shows what core knows
            _log.debug("provider status failed for %s", plugin, exc_info=True)
        try:
            from backend.uefn_plugins import live

            out["live"] = live.status(plugin)
        except Exception:  # noqa: BLE001 — no Live button this read
            _log.debug("live status failed for %s", plugin, exc_info=True)
    if out.get("binding"):  # a switch is getting a team's folder files first
        out["state"] = "binding"
    return out


def team_overview(space: str = "") -> dict[str, Any]:
    """The Team modal, whatever tab is on screen: one team copy (``space``; '' picks the one
    most plugins show) with each plugin on it (Workflows too): ``pluginId``, ``label``,
    ``icon``, ``source``, ``incoming`` / ``incomingBy`` / ``held``, ``outgoing`` (rows),
    ``pending``, ``conflicts``, ``blocked`` (downloads that failed), ``state``, ``error``.
    No plugin on a team: ``current`` is ``personal`` and ``plugins`` is empty."""
    from collections import Counter

    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes
    from backend.uefn_plugins.store import get_enabled_plugin_ids, load_plugin_manifest, plugin_icon_data_url

    account = scopes.account_id()
    enabled = set(get_enabled_plugin_ids()) | {WORKFLOW_DOCS}
    links = {p: s for p, s in repo.links(account).items() if p in enabled and s != scopes.PERSONAL} \
        if account != scopes.LOCAL else {}
    space = str(space or "") or (Counter(links.values()).most_common(1)[0][0] if links else "")
    if not space:
        return {"ok": True, "current": scopes.PERSONAL, "label": "Local", "plugins": []}
    rows: list[dict[str, Any]] = []
    for pid in sorted(p for p, s in links.items() if s == space):
        try:
            st = scope_status(pid)
        except Exception:  # noqa: BLE001 — one plugin's error never hides the others
            _log.debug("team overview of %s failed", pid, exc_info=True)
            continue
        ch = st.get("changes") or {}
        workflows = pid == WORKFLOW_DOCS
        try:
            manifest = {} if workflows else (load_plugin_manifest(pid) or {})
        except ValueError:
            manifest = {}
        rows.append({
            "pluginId": pid, "label": "Workflows" if workflows else str(st.get("pluginLabel") or pid),
            "icon": "" if workflows else plugin_icon_data_url(pid) or "",
            "source": "workflows" if workflows else str(manifest.get("source") or "local"),
            "incoming": int(ch.get("incoming") or 0), "incomingBy": list(ch.get("incomingBy") or []),
            "held": bool(ch.get("held")), "outgoing": list(ch.get("outgoing") or []),
            "pending": int(st.get("pending") or 0), "conflicts": len(st.get("conflicts") or []),
            "blocked": list(ch.get("blocked") or []), "state": str(st.get("state") or ""),
            "error": str(st.get("error") or ""), "items": sum(int((st.get("current") or {}).get(k) or 0) for k in ("docs", "files")),
        })
    rows.sort(key=lambda r: (r["pluginId"] == WORKFLOW_DOCS, r["label"].casefold()))
    label = str(repo.sync_get(account, space).get("label") or "") or "Team"
    return {"ok": True, "current": space, "label": label, "plugins": rows}


def _people(account: str, space: str, plugin: str) -> list[dict[str, Any]]:
    """The space's roster (the last heads or beat) as the bar reads it: each person's
    name, PC label and what they do in ``plugin``; ``me`` marks this PC's own row."""
    from backend.store.repos import space_vcs

    roster = space_vcs.presence_get(account, space)["roster"]
    people = roster.get("people") if isinstance(roster, dict) else None
    me = space_vcs.device_id()
    try:
        from backend.uefn_plugins.vcs_engine import my_user_id

        my_user = my_user_id()
    except Exception:  # noqa: BLE001
        my_user = ""
    out: list[dict[str, Any]] = []
    for p in people if isinstance(people, list) else []:
        if not isinstance(p, Mapping):
            continue
        plugins = p.get("plugins") if isinstance(p.get("plugins"), Mapping) else {}
        here = plugins.get(plugin) if isinstance(plugins.get(plugin), Mapping) else {}
        editing = [
            {"unitName": str(e.get("unitName") or ""), "actor": "agent" if e.get("actor") == "agent" else "user",
             "ducky": str(e.get("ducky") or ""), "since": float(e.get("since") or 0.0)}
            for e in here.get("editing") or [] if isinstance(e, Mapping)
        ]
        room = here.get("live") if isinstance(here.get("live"), Mapping) else {}
        out.append({
            "name": person(p.get("user_name"), p.get("user"), fallback="A teammate"),
            "user": str(p.get("user") or ""),
            "device": str(p.get("label") or ""),
            "deviceId": str(p.get("device") or ""),  # the live peers' ``device``
            "me": str(p.get("device") or "") == me,
            # This account on another PC: "You on MainPC", locked out here like anyone else.
            "self": bool(my_user) and str(p.get("user") or "") == my_user and str(p.get("device") or "") != me,
            "plugins": {plugin: {"viewing": bool(here.get("viewing")), "editing": editing,
                                 "live": str(room.get("room") or "")}},
        })
    return out


def _lock_holder(account: str, space: str, lk: Mapping[str, Any]) -> dict[str, str]:
    """``holder`` ('' for this PC's own lock, "AI (your ducky Builder)" when a ducky here
    holds it) and ``elsewhere`` (the PC's name when this account holds it on another PC:
    "you on MainPC")."""
    if lk["mine"]:
        try:
            from backend.uefn_plugins.vcs_engine import holder_of, lock_ducky

            return {"holder": holder_of(account, space, lk)[0] if lock_ducky(account, space, lk) is not None
                    else "", "elsewhere": ""}
        except Exception:  # noqa: BLE001
            return {"holder": "", "elsewhere": ""}
    try:
        from backend.uefn_plugins.vcs_engine import holder_of

        who, pc = holder_of(account, space, lk)
    except Exception:  # noqa: BLE001 — a name still shows
        who, pc = person(lk["holder_name"], lk["holder_user"]), ""
    return {"holder": who, "elsewhere": pc}


def team_fields(account: str, space: str, plugin: str, *, state: str = "ok") -> dict[str, Any]:
    """What the bar and its drawer show of a team copy, beyond the sync row: ``engine``
    (``v1``: the provider's older sync, ``v2``: the new one), ``changes.outgoing`` with each
    one's state, ``conflicts`` and ``importing`` (%). Only on the new sync: who is in the
    space (``people``), live ``locks`` on this plugin, ``changes.incoming`` changesets and
    ``changes.blocked`` incoming items, ``mode``, ``head`` (the last changeset applied
    here), ``nextTry``; a failed round shows as state ``offline`` / ``error`` with its
    reason, a paused one as ``paused``, and ``pending`` counts only what is queued. A lost
    space adds ``unsent`` (changes never sent) and ``unsentChosen``."""
    import re

    from backend.store.repos import plugin_data as repo
    from backend.store.repos import space_vcs
    from backend.uefn_plugins.vcs_engine import unit_label

    def name(kind: str, key: str) -> str:
        return unit_label(account, space, plugin, f"{kind}:{key}") if kind in ("doc", "asset") else key

    now = time.time()
    head = space_vcs.head_get(account, space, plugin)
    v2 = head["engine"] == "v2"
    sending = space_vcs.cs_items_open(account, space, plugin)
    outgoing = []
    for r in repo.pending_items(account, space, plugin):
        queued = r["sync_state"] or ("sending" if (r["kind"], r["key"]) in sending else "queued")
        outgoing.append({"kind": r["kind"], "key": r["key"], "name": name(r["kind"], r["key"]), "size": int(r["size"]),
                         "deleted": bool(r["deleted"]), "state": queued, "reason": r["sync_error"],
                         "attempts": int(r["attempts"])})
    pct = re.search(r"(\d+)%", str(head["error"] or "")) if head["state"] == "importing" else None
    out: dict[str, Any] = {
        "engine": "v2" if v2 else "v1",
        "changes": {"outgoing": outgoing},
        "conflicts": [{"kind": c["kind"], "key": c["key"], "name": name(c["kind"], c["key"]), "unit": c["unit"],
                       "reason": c["reason"], "by": person(c["theirs_by"]) if c["theirs_by"] else "",
                       "at": c["theirs_at"],
                       "theirsSeq": int(c["theirs_seq"])} for c in space_vcs.conflicts(account, space, plugin)],
        "importing": float(pct.group(1)) if pct else None,
    }
    if v2:
        out.update(
            head=int(head["seq"]),
            mode=head["mode"],
            people=_people(account, space, plugin),
            locks=[{"unit": lk["unit"], "name": lk["name"] or lk["unit"].split(":", 1)[-1],
                    **_lock_holder(account, space, lk),
                    "mine": lk["mine"], "since": lk["since"],
                    "leaseLeft": lk["lease_left"]} for lk in space_vcs.locks(account, space, plugin, now=now)],
            nextTry=float(head["next_try"]) if float(head["next_try"]) > now else None,
            pending=sum(1 for o in outgoing if o["state"] in ("queued", "sending")),
        )
        out["changes"].update(
            incoming=max(0, int(head["remote_seq"]) - int(head["seq"])),
            blocked=[{"kind": b["kind"], "key": b["key"], "name": name(b["kind"], b["key"]), "error": b["error"],
                      "attempts": int(b["attempts"])} for b in space_vcs.blocked(account, space, plugin)],
        )
        if state == "ok" and head["state"] in ("offline", "error", "retry"):
            out.update(state="offline" if head["state"] == "offline" else "error", error=str(head["error"] or ""))
        elif state == "ok" and head["state"] == "paused":
            out.update(state="paused", error=str(head["error"] or ""))
    if state == "lost":
        out.update(unsent=space_vcs.pending_unsent(account, space),
                   unsentChosen=bool(space_vcs.lost_choice_get(account, space)))
    from backend.uefn_plugins import team_folders

    if team_folders.plugin_folders(plugin):
        _team_folder_fields(out, account, space, plugin)
    return out


def _folder_fields(plugin: str) -> dict[str, Any]:
    """A plugin that keeps its own folders: ``teamFolders`` (each declared folder: ``id``,
    ``refused`` (why it can't be shared from this PC), ``adopted``, ``bound``,
    ``afterPull``), ``folderOffers`` (teams the bar offers to copy Local into),
    ``folderNote`` (its files are plaintext on each PC) and, while a switch gets a team's
    copy first, state ``binding`` with ``binding`` (``{team, pct, text}``: "Getting
    Alpha's files… 40%")."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import team_folders

    if not team_folders.plugin_folders(plugin):
        return {}
    st = team_folders.status(plugin)
    out: dict[str, Any] = {"teamFolders": st["folders"], "folderOffers": st["offers"], "folderNote": st["note"]}
    progress = space_vcs.binding_get(plugin)
    out["binding"] = None if progress is None else {
        **progress, "text": team_folders.PULLING.format(team=progress["team"] or "the team", pct=progress["pct"])}
    return out


def _team_folder_fields(out: dict[str, Any], account: str, space: str, plugin: str) -> None:
    """The team copy's folder units for the bar and drawer: ``folders`` (each unit:
    ``folder_id``, ``unit``, ``unitId``, ``name``, ``state`` up_to_date | changed_here |
    locked_by | conflict | incoming | quarantined | refused, ``holder``, ``mine``),
    folder units in ``changes.outgoing`` (and ``pending``), ``changes.held`` (incoming
    changes wait for Update now) with ``changes.incoming`` and ``changes.incomingBy``
    ("2 updates from Ana · Update now"), and folder unit conflicts and set-aside downloads
    named by their unit and file."""
    from backend.store.repos import folder_files, space_vcs
    from backend.uefn_plugins import vcs_engine

    rows = vcs_engine.folder_rows(account, space, plugin, ledger_for(account, space, plugin))
    out["folders"] = rows
    outgoing = vcs_engine.folder_outgoing(account, space, plugin)
    changes = out.setdefault("changes", {})
    changes["outgoing"] = [*changes.get("outgoing", []), *outgoing]
    waiting = space_vcs.incoming_queue_get(account, space, plugin)
    changes["held"] = bool(waiting["count"])
    changes["incomingBy"] = list(dict.fromkeys(person(b) for b in waiting["by"]))
    if waiting["count"]:
        changes["incoming"] = max(int(changes.get("incoming") or 0), waiting["count"])
    for b in changes.get("blocked") or []:
        if b.get("kind") == "tree":
            row = folder_files.by_key(account, space, plugin, str(b["key"]))
            b["name"] = row["relpath"] if row else "A file of this plugin's folder"
    if "pending" in out:
        out["pending"] = int(out["pending"]) + sum(1 for o in outgoing if o["state"] in ("queued", "sending"))
    names = {(r["folder_id"], r["unit"]): r["name"] for r in rows}
    for c in out.get("conflicts") or []:
        if c.get("kind") == "tree":
            fid, _, unit = str(c["key"]).partition("/")
            c.update(folder_id=fid, unit=unit, name=names.get((fid, unit), unit))


def scope_choices(purpose: str = "data") -> dict[str, Any]:
    """Local, plus the spaces the provider offers for ``purpose``. Pickers only."""
    from backend.uefn_plugins import scopes

    choices: list[dict[str, Any]] = [{"id": scopes.PERSONAL, "kind": "personal", "label": "Local"}]
    if scopes.account_id() != scopes.LOCAL:
        for row in provider().choices(purpose) or []:
            sid = str((row or {}).get("id") or "")
            if scopes.valid_space_id(sid):
                choices.append({**row, "id": sid, "kind": "shared", "label": str(row.get("label") or sid)})
    return {"ok": True, "choices": choices, "kindLabel": provider().kind_label,
            "emptyNote": provider().choices_note(purpose)}


def link_scope(plugin: str, scope_id: str) -> dict[str, Any]:
    """Keep ``plugin``'s data in Local or one shared space (the UI confirmed first). Auto
    locks this PC held on the copy it leaves are released."""
    from backend.uefn_plugins import scopes

    before = scopes.active_scope(plugin)
    scopes.link_plugin(plugin, scope_id)
    if before["kind"] == "shared" and before["id"] != scope_id:
        threading.Thread(target=release_auto_locks, args=(before["account"], before["id"], plugin),
                         name="space-lock-release", daemon=True).start()
    return scope_status(plugin)


def switch_folders(plugin: str, scope_id: str, *, first_share: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """The folder part of a plugin's switch, before it is linked to ``scope_id`` (None: the
    plugin keeps no folders). Leaving a team: its changed units go out first (up to 30 s).
    To a team: the team's copy comes down first while the plugin still reads its current
    copy ("Getting Alpha's files… 40%" on the bar), and a failed pull changes nothing; an
    empty team copy is bound only after the first share (``{needs: "first_share", units,
    note}`` until ``first_share`` is ``{"units": [...]}`` or ``{"startEmpty": true}``;
    ``{"pick": true}`` asks with the Local units the team lacks even when it has files).
    Then, once the plugin's calls are done, its folders point at the new copy. ``ok``
    false: nothing was linked; the caller links on ``ok``."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes, team_folders, vcs_engine

    if not team_folders.plugin_folders(plugin):
        return None
    leaving = scopes.active_scope(plugin)
    if leaving["kind"] == "shared" and leaving["id"] != scope_id and leaving["state"] not in ("lost", "reset"):
        backend = ledger_for(leaving["account"], leaving["id"], plugin)
        if backend is not None:
            vcs_engine.send_before_leaving(leaving["account"], leaving["id"], plugin, backend)
    if scope_id == scopes.PERSONAL:
        return team_folders.switch(plugin, scope_id)
    account = scopes.account_id()
    backend = ledger_for(account, scope_id, plugin) if account != scopes.LOCAL else None
    label = str(repo.sync_get(account, scope_id)["label"] or "") if account != scopes.LOCAL else ""

    def pull() -> tuple[bool, str]:
        if backend is None:
            return False, NO_LEDGER_ERROR if has_provider() else NO_PROVIDER_ERROR
        return vcs_engine.pull_copy(account, scope_id, plugin, backend, label=label)

    def push() -> tuple[bool, str]:
        if backend is None:
            return False, NO_LEDGER_ERROR if has_provider() else NO_PROVIDER_ERROR
        return vcs_engine.send_copy(account, scope_id, plugin, backend, label=label)

    share: dict[str, Any] | None = None
    if first_share:
        share = {"units": [str(u) for u in first_share.get("units") or []],
                 "start_empty": bool(first_share.get("startEmpty") or first_share.get("start_empty")),
                 "pick": bool(first_share.get("pick"))}
    return team_folders.switch(plugin, scope_id, pull=pull, push=push, first_share=share)


def project_folder_plugins() -> list[str]:
    """Enabled plugins that keep a folder inside the open project (a ``{PROJECT}`` root)."""
    from backend.uefn_plugins import team_folders
    from backend.uefn_plugins.store import get_enabled_plugin_ids

    return sorted(p for p in set(get_enabled_plugin_ids())
                  if any(f.project for f in team_folders.plugin_folders(p)))


def rebind_project_folders(project_root: str, plugins: list[str] | None = None) -> list[str]:
    """The open project changed: each enabled plugin with a ``{PROJECT}`` folder (or each of
    ``plugins``) points it at the copy it reads for that project. On a team, the team's
    copy of the new project comes down first and is used once it has files; with none
    yet, the bar offers to copy Local into the team. What that pull changed in the copies
    the plugin uses is told to it as any pull is (a ``reload`` plugin restarts). A plugin
    whose folder moved restarts (every process reloads it before its next call). Returns
    the plugins whose folders moved or that restart for the pull."""
    from backend.store.repos import folder_files
    from backend.uefn_plugins import scopes, team_folders, vcs_engine

    moved: list[str] = []
    for plugin in project_folder_plugins() if plugins is None else sorted(set(plugins)):
        folders = [f for f in team_folders.plugin_folders(plugin) if f.project]
        if not folders:
            continue
        try:
            before = folder_files.reload_epoch(plugin)
            scope = scopes.active_scope(plugin)
            if scope["kind"] == "shared" and project_root:
                account, space = scope["account"], scope["id"]
                backend = ledger_for(account, space, plugin)
                pulled, landed = False, {"tree": 0}
                if backend is not None:
                    pulled, _why = vcs_engine.pull_copy(account, space, plugin, backend,
                                                        label=str(scope.get("label") or ""), landed=landed)
                if landed["tree"]:  # the pull changed files the plugin may be using: it hears it (or restarts)
                    vcs_engine.after_pull(plugin)
                for b in team_folders.resolve(plugin, project_root=project_root):
                    if not pulled or not b.folder.project or b.root is None or b.refused:
                        continue  # a pull that didn't finish says nothing about what the team has
                    copy = team_folders.team_copy(account, space, plugin, b.folder, b.project_id)
                    if team_folders._has_files(copy, b.folder):
                        folder_files.copy_put(account, space, plugin, b.folder.id, b.project_id, ready=1,
                                              pulled_at=time.time())
                    elif not folder_files.copy_get(account, space, plugin, b.folder.id, b.project_id)["ready"]:
                        folder_files.copy_put(account, space, plugin, b.folder.id, b.project_id, offer=1)
            team_folders.bind_plugin(plugin, project_root=project_root)
            if folder_files.reload_epoch(plugin) != before:
                moved.append(plugin)
        except Exception:  # noqa: BLE001 — its bind before load tries again
            _log.warning("pointing %s's project folder at its copy failed", plugin, exc_info=True)
    if not moved:
        return moved
    from backend.process_role import is_app

    if is_app():  # tool servers and agent hosts reload on the plugin's next call (epoch)
        from backend.uefn_plugins.host import reload_single_plugin

        for plugin in moved:
            try:
                reload_single_plugin(plugin)
            except Exception:  # noqa: BLE001 — its next call reloads it through the epoch
                _log.debug("restart after a project switch failed for %s", plugin, exc_info=True)
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "plugin_scope_changed", "plugins": moved, "switched": True})
    except Exception:  # noqa: BLE001 — panels read the new copy on their next open
        _log.debug("telling panels about the project's folders failed", exc_info=True)
    return moved


def release_auto_locks(account: str, space: str, plugin: str | None = None) -> int:
    """Release the auto locks this PC holds in the space (``plugin``'s only, when given):
    a scope switch, the app closing. Manual locks stay until released. Best effort: a
    lease the server can't hear about runs out within 5 minutes. Returns how many."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import vcs_engine

    units: dict[str, list[str]] = {}
    for lk in space_vcs.locks(account, space, plugin, live=False):
        if lk["mine"] and lk["reason"] == "auto":
            units.setdefault(lk["plugin_id"], []).append(lk["unit"])
    for pid, held in units.items():
        backend = ledger_for(account, space, pid)
        if backend is not None:
            vcs_engine.release(account, space, pid, backend, held)
        else:
            space_vcs.lock_delete(account, space, pid, held)
    return sum(len(held) for held in units.values())


def on_account_change(before: str, after: str) -> None:
    """Sign-in, sign-out or another account (account ids). Every plugin that showed a
    shared space's copy, or shows one now, restarts on the copy it reads now and its
    open panels reload, like a switch: signing out puts them all on Local instead of
    leaving the space's data on screen. Folders a plugin keeps itself point at the copy it
    reads now first (Local's after a sign-out), whether the plugin is on or not."""
    from backend.store.repos import folder_files
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes

    folders = sorted({lk["plugin_id"] for lk in folder_files.links() if lk["account_id"] != folder_files.LOCAL_ACCOUNT})
    plugins = sorted(
        {
            plugin
            for account in {before, after} - {scopes.LOCAL}
            for plugin, space in repo.links(account).items()
            if scopes.valid_space_id(space) and scopes.sharing_allowed(plugin)
        }
        | set(folders)
    )
    if not plugins:
        return

    def _work() -> None:
        from backend.uefn_plugins import team_folders
        from backend.uefn_plugins.host import reload_single_plugin
        from backend.uefn_plugins.store import get_enabled_plugin_ids
        from frontend.ui_web.agent_modes import push_ui_event

        for plugin in folders:
            try:
                team_folders.bind_plugin(plugin)
            except Exception:  # noqa: BLE001 — its bind before load tries again
                _log.warning("pointing %s's folders at the copy it reads now failed", plugin, exc_info=True)
        enabled = set(get_enabled_plugin_ids())
        for plugin in plugins:
            if plugin in enabled:
                try:
                    reload_single_plugin(plugin)
                except Exception:  # noqa: BLE001 — its panels still reload on the new copy
                    _log.debug("restart after account change failed for %s", plugin, exc_info=True)
        push_ui_event({"type": "plugin_scope_changed", "plugins": plugins, "switched": True})

    threading.Thread(target=_work, name="space-account-change", daemon=True).start()


# --------------------------------------------------------------------------- team copies: the drawer + agents
#
# What the bar's drawer and the agents' team tools do with a plugin's copy on the new
# sync (a ledger): locks, conflicts, history, restore, Auto/Manual, Push and Pull, and the
# unsent changes of a space this account lost.


def _team(plugin: str, *, need_ledger: bool = True) -> tuple[str, str, Any]:
    """``(account, space, ledger)`` of ``plugin``'s shared copy. ValueError with the reason
    when it is Local, or (``need_ledger``) the copy isn't on the new sync."""
    from backend.uefn_plugins import scopes

    if not scopes.valid_plugin_id(plugin):
        raise ValueError(f"invalid plugin id: {plugin!r}")
    scope = scopes.active_scope(plugin)
    if scope["kind"] != "shared":
        raise ValueError(f"{plugin_label(plugin) or plugin}'s data is Local, on this PC only.")
    backend = ledger_for(scope["account"], scope["id"], plugin) if need_ledger else None
    if need_ledger and backend is None:
        raise ValueError(NO_LEDGER_ERROR if has_provider() else NO_PROVIDER_ERROR)
    return scope["account"], scope["id"], backend


def team_unit(plugin: str, unit: str) -> str:
    """A unit as given (``doc:<key>``, ``asset:<path>``, ``tree:...``), or a bare key: the
    doc of that name, else the file of that path."""
    from backend.uefn_plugins import scopes

    unit = str(unit or "").strip()
    kind, sep, key = unit.partition(":")
    if sep and kind in ("doc", "asset", "tree"):
        ok = scopes.valid_doc_key(key) if kind == "doc" else scopes.valid_asset_path(key) if kind == "asset" else bool(key)
        if ok:
            return unit
        raise ValueError(f"invalid unit: {unit!r}")
    if scopes.valid_doc_key(unit) and scopes.PluginData(plugin).get(unit) is not None:
        return f"doc:{unit}"
    if scopes.valid_asset_path(unit) and scopes.PluginData(plugin).has_file(unit):
        return f"asset:{unit}"
    if scopes.valid_doc_key(unit):
        return f"doc:{unit}"
    raise ValueError(f"invalid unit: {unit!r} (use doc:<key> or asset:<path>)")


def _changed(plugin: str, *, data: bool = True) -> None:
    """The plugin's bars read their status again; ``data``: its panels re-read too (a
    resolve or restore changed its data; a lock or the mode didn't)."""
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "plugin_scope_changed", "plugins": [plugin], **({} if data else {"status": True})})
    except Exception:  # noqa: BLE001 — the next status read shows it
        _log.debug("announcing a team change failed", exc_info=True)


def team_lock(plugin: str, unit: str, action: str = "acquire", *, name: str = "") -> dict[str, Any]:
    """``acquire`` (a manual lock, kept until released), ``release``, ``break`` (owners and
    admins, or anyone once the lease ran out) or ``ask`` (the holder is asked to release)."""
    from backend.uefn_plugins import vcs_engine

    account, space, backend = _team(plugin)
    folder_unit = _folder_unit_id(plugin, space, backend, unit)
    if folder_unit:
        unit, name = folder_unit[0], name or folder_unit[1]
    else:
        unit = team_unit(plugin, unit)
    if action == "acquire":
        got = vcs_engine.acquire(account, space, plugin, backend, [(unit, name or unit.split(":", 1)[-1])])[unit]
        out = {k: v for k, v in got.items() if k != "unit"}
    elif action == "release":
        out = vcs_engine.release(account, space, plugin, backend, [unit])
    elif action == "break":
        out = vcs_engine.break_lock(account, space, plugin, backend, unit)
    elif action == "ask":
        out = vcs_engine.ask(account, space, plugin, backend, unit)
    else:
        raise ValueError("action must be acquire, release, break or ask")
    _changed(plugin, data=False)
    return {"unit": unit, **out}


def _folder_unit_id(plugin: str, space: str, backend: Any, unit: str) -> tuple[str, str] | None:
    """A folder unit of a plugin that keeps its own folders, named ``<folder>/<unit>`` (or
    ``tree:<folder>/<unit>``), as locks name it: (unit id, name). None for anything else,
    an id already (``tree:<folder>/<hash>``) included."""
    import re

    from backend.store.repos import folder_files
    from backend.uefn_plugins import scopes, team_folders

    text = str(unit or "").strip()
    named = text[len("tree:"):] if text.startswith("tree:") else text
    folder_id, sep, rest = named.partition("/")
    folder = team_folders.folder_of(plugin, folder_id) if sep and rest else None
    if folder is None:
        return None
    if text.startswith("tree:"):
        known = {r["unit"] for r in folder_files.files(scopes.account_id(), space, plugin, folder.id)}
        if rest not in known and re.fullmatch(r"[0-9a-f]{32}", rest):
            return None  # the id itself
    name = rest.split("/", 1)[-1] if folder.project and "/" in rest else rest
    return team_folders.tree_unit(backend, space, folder.id, rest), name


def team_folder_unit(plugin: str, folder_id: str, unit: str, choice: str) -> dict[str, Any]:
    """Settle a folder unit: after a conflict ``mine`` (Keep mine), ``theirs`` (Take theirs)
    or ``both`` (Keep both); changed while someone else holds it, ``keep_copy`` (Keep mine as
    a copy) or ``discard`` (Discard mine). What is kept goes out on the next round, and what
    comes back from the team comes down on it."""
    from backend.uefn_plugins import vcs_engine

    account, space, _ = _team(plugin, need_ledger=False)
    out = vcs_engine.resolve_unit(account, space, plugin, ledger_for(account, space, plugin), str(folder_id),
                                  str(unit), str(choice))
    if out.get("ok"):
        _changed(plugin)
        sync_space(account, space, force=True)
    return out


def _unit_text(account: str, space: str, plugin: str, folder_id: str, unit: str) -> str:
    """Compare a folder unit: the files changed here, and the team's files waiting."""
    from backend.store.repos import folder_files, space_vcs
    from backend.uefn_plugins import vcs_engine

    mine = [r["relpath"] for r in folder_files.files(account, space, plugin, folder_id, unit=unit) if r["dirty"]]
    c = space_vcs.conflict_get(account, space, plugin, "tree", f"{folder_id}/{unit}")
    theirs = vcs_engine._held_items(c)
    who = person(c["theirs_by"]) if c and c["theirs_by"] else "the team"
    lines = [f"Changed here: {len(mine)} file(s)", *[f"  {rel}" for rel in mine]]
    lines += [f"Changed by {who}: {len(theirs)} file(s)",
              *[f"  {rel}{' (deleted)' if op == 'del' else ''}" for _k, _s, op, rel in theirs]]
    return "\n".join(lines)


def _conflict_text(account: str, space: str, plugin: str, kind: str, key: str) -> str:
    """Compare: the team's version (current here) against mine, as a line diff of the
    pretty JSON for a doc, of the text for a text file, else sizes and checksums."""
    import difflib
    import json
    from pathlib import Path

    from backend.store.repos import space_vcs
    from backend.uefn_plugins import data_crypto, scopes

    c = space_vcs.conflict_get(account, space, plugin, kind, key)
    if c is None:
        raise ValueError("There is no conflict here any more.")
    row = space_vcs.row(None, account, space, plugin, kind, key)
    live = row is not None and not int(row["deleted"])
    theirs_deleted = bool(int(c["theirs_deleted"])) or not live
    mine_deleted = bool(int(c["mine_deleted"]))
    if kind == "doc":
        def pretty(sealed: str | None) -> list[str]:
            if not sealed:
                return []
            return json.dumps(json.loads(data_crypto.open_text(sealed, account)), indent=2, sort_keys=True,
                              ensure_ascii=False).splitlines()

        theirs = [] if theirs_deleted else pretty(row["value"] if row else None)
        mine = [] if mine_deleted else pretty(c["mine_value"])
    else:
        def text(path: str) -> list[str] | None:
            try:
                raw = data_crypto.open_bytes(Path(path).read_bytes(), account)
                return raw.decode("utf-8").splitlines() if len(raw) <= 1024 * 1024 else None
            except (OSError, UnicodeDecodeError, ValueError):
                return None

        target = str(scopes.asset_file({"account": account, "id": space}, plugin, key))
        theirs = [] if theirs_deleted else text(target)
        mine = [] if mine_deleted or not c["mine_path"] else text(c["mine_path"])
        if theirs is None or mine is None:
            def line(who: str, gone: bool, size: int, sha: str, by: str) -> str:
                return f"{who}: deleted" if gone else f"{who}: {size:,} bytes, sha256 {sha[:12]}{by}"

            by = f", by {person(c['theirs_by'])}" if c["theirs_by"] else ""
            return "\n".join([
                line("Theirs", theirs_deleted, int(row["size"]) if row else 0, str(row["sha256"]) if row else "", by),
                line("Mine", mine_deleted, int(c["mine_size"]), str(c["mine_sha"]), ""),
            ])
    who = person(c["theirs_by"]) if c["theirs_by"] else "theirs"
    return "\n".join(difflib.unified_diff(theirs, mine, fromfile=f"{who}'s", tofile="mine", lineterm=""))


def team_resolve(plugin: str, kind: str, key: str, choice: str) -> dict[str, Any]:
    """Settle a conflict: ``mine`` (Keep mine, Restore mine), ``theirs`` (Keep theirs,
    Accept delete), or ``compare`` (``diff``, nothing changes). ``both`` is for folder
    units (kind ``tree``, key ``<folder>/<unit>``). What is kept goes out on the next round."""
    from backend.uefn_plugins import vcs_engine

    account, space, _ = _team(plugin, need_ledger=False)
    if kind == "tree":
        folder_id, _, unit = str(key).partition("/")
        if choice == "compare":
            return {"ok": True, "diff": _unit_text(account, space, plugin, folder_id, unit)}
        return team_folder_unit(plugin, folder_id, unit, choice)
    if kind not in ("doc", "asset"):
        raise ValueError("kind must be doc, asset or tree")
    if choice == "compare":
        return {"ok": True, "diff": _conflict_text(account, space, plugin, kind, key)}
    if choice == "both":
        return {"ok": False, "error": "Keep both is for folder units; pick mine or theirs."}
    out = vcs_engine.resolve(account, space, plugin, kind, key, choice, ledger_for(account, space, plugin))
    if out.get("ok"):
        _changed(plugin)
        sync_space(account, space, force=True)
    return out


def team_restore(plugin: str, *, items: list[Mapping[str, Any]] | None = None, to_seq: int | None = None,
                 undo_seq: int | None = None, message: str = "") -> dict[str, Any]:
    """Items back to their version at a seq, the plugin as of ``to_seq``, or changeset
    ``undo_seq`` undone: a new changeset everyone gets, then pulled here."""
    from backend.uefn_plugins import scopes, vcs_engine

    account, space, backend = _team(plugin)
    if undo_seq:
        out = vcs_engine.undo(account, space, plugin, backend, int(undo_seq), message=message)
    elif items:
        wanted: dict[tuple[str, str], int] = {}
        for it in items:
            kind, key = str(it.get("kind") or ""), str(it.get("key") or "")
            if kind not in ("doc", "asset") or not (scopes.valid_doc_key(key) if kind == "doc"
                                                    else scopes.valid_asset_path(key)):
                raise ValueError(f"invalid item: {kind}:{key}")
            wanted[(kind, key)] = int(it.get("seq") or 0)
        out = vcs_engine.restore(account, space, plugin, backend, items=wanted, message=message)
    elif to_seq is not None and int(to_seq) >= 0:
        out = vcs_engine.restore(account, space, plugin, backend, to_seq=int(to_seq),
                                 message=message or f"Restored to #{int(to_seq)}")
    else:
        raise ValueError("Say what to restore: items, toSeq or undoSeq.")
    if out.get("ok"):
        _changed(plugin)
    return out


def team_history(plugin: str, *, kind: str = "", key: str = "", before: int | None = None,
                 limit: int = 50) -> dict[str, Any]:
    """Changesets newest first, or one item's versions (``kind`` + ``key``), before ``before``."""
    from backend.uefn_plugins import vcs_engine

    account, space, backend = _team(plugin)
    item = (kind, key) if kind and key else None
    out = vcs_engine.history(account, space, plugin, backend, key=item, before=before or None,
                             limit=max(1, min(int(limit or 50), 200)))
    names = vcs_engine.device_names(account, space) if out.get("entries") else {}
    for entry in out.get("entries") or []:
        entry["items"] = [list(i) for i in entry.get("items") or []]
        if entry.get("by"):
            entry["by"] = person(entry["by"])
        # "device" is the PC's name, never its id: the name the change carries, else one a
        # roster gave that id, else blank. "deviceId" keeps the id.
        device = str(entry.get("device") or "")
        entry["deviceId"] = device
        entry["device"] = str(entry.pop("device_name", "") or "") or names.get(device, "")
        author, author_pc = str(entry.pop("author", "") or ""), str(entry.pop("author_pc", "") or "")
        if author:  # sent by a live group's leader ("via") for the groupmate who made it
            via = str(entry.get("by") or "")
            entry.update(by=author, device=author_pc or entry["device"])
            if person(author) != via:  # this account on its other PC: the PC's name says it all
                entry["via"] = via
    return out


def team_mode(plugin: str, mode: str) -> dict[str, Any]:
    """Auto (rounds push and pull by themselves) or Manual (only Push and Pull do)."""
    from backend.uefn_plugins import vcs_engine

    account, space, _ = _team(plugin)  # Manual mode is the new sync's; the older one never reads it
    out = vcs_engine.set_mode(account, space, plugin, mode)
    _changed(plugin, data=False)
    return out


def team_sync(plugin: str, *, push: bool | None = None, pull: bool | None = None, message: str = "",
              kind: str = "", key: str = "", on_done: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Push (with an optional message) or Pull now, on a worker thread, whatever the mode.
    ``kind`` + ``key``: download that incoming item again first. ``on_done(result)`` after."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import vcs_engine

    account, space, backend = _team(plugin)
    if kind and key:
        space_vcs.blocked_retry_now(account, space, plugin, kind, key)

    def _work() -> None:
        try:
            out = vcs_engine.run(account, space, plugin, backend, push=push, pull=pull, force=True, message=message)
        except Exception as exc:  # noqa: BLE001 — recorded like any round that raised
            out = _round_failed({"account": account, "id": space}, exc)
        if out.get("tree"):  # its folder files changed: reload it, or tell its panels
            vcs_engine.after_pull(plugin)
        if on_done is not None:
            on_done({**out, "keys": {plugin: list(out.get("keys") or [])}})

    threading.Thread(target=_work, name="space-team-sync", daemon=True).start()
    return {"ok": True, "started": True}


def save_unsent(plugin: str, action: str = "save") -> dict[str, Any]:
    """A space this account lost: copy this PC's changes never sent into Local (``save``:
    same key when Local has none, else ``<key>-from-<space>``) or let them go with the
    copy (``discard``). Either way the copy here may then leave this PC."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import vcs_engine

    account, space, _ = _team(plugin, need_ledger=False)
    if repo.sync_get(account, space)["state"] != "lost":
        raise ValueError("Only for a space you lost access to.")
    if action == "discard":
        return vcs_engine.discard_unsent(account, space)
    if action != "save":
        raise ValueError("Choose save or discard.")
    out = vcs_engine.save_unsent_to_local(account, space, label=repo.sync_get(account, space)["label"])
    _changed(plugin)
    if any(s["plugin"] == WORKFLOW_DOCS for s in out.get("saved") or []):
        try:
            from frontend.ui_web.agent_modes import push_ui_event

            push_ui_event({"type": "graphs_changed"})
        except Exception:  # noqa: BLE001 — the Workflows view reads it on its next open
            pass
    return out
