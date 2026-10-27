"""The version engine of a shared space's copy: push and pull rounds per (space, plugin)
against the provider's ledger backend (:mod:`backend.uefn_plugins.ledger`).

``plugin_data`` is this PC's working copy (``rev`` = the item's seq on the server).

Push. Dirty rows become changesets: one per closed write group (the rows one tool call
or panel call wrote; a big group goes as parts of at most :data:`MAX_ITEMS`, the last one
marked), and the plugin's other rows in changesets of at most :data:`MAX_ITEMS` each.
Each changeset is written to ``space_changesets`` before anything is uploaded, so a crash
replays the same cs_id; a row another call rewrote since stays out of it, and a file
rewritten while it was read for the upload goes again. Rows under someone else's live
lock are ``locked_out`` and stay here. The answer:

- committed or replay: per-row compare-and-set (:func:`space_vcs.mark_committed`): a row
  edited meanwhile stays dirty on the new base; a replay is checked against what the
  first try sent, so an edit made since is never taken as sent;
- conflict: pull to the head (merging or keeping both), rebuild, retry; three moves in a
  row back off 30 to 120 s;
- locked, refused: those rows wait with the reason; the rest go on;
- error (the server took none of it and said why; also an upload that went missing
  twice): a changeset's first try steps aside and the others go on. Rows of no write group
  go again at once in two halves, no try counted, so the row the server won't take is
  found while the rest land. Anything else (a write group with its unsent parts, a folder
  unit) counts a try once the server took or answered another changeset in the round, and
  is ``blocked`` with the reason after the last (Retry, Drop my change). When it answered
  none, or two fail in a row, the server is taken to refuse the plugin: no try counts and
  the round backs off. An error to a resend (its first answer got lost) or to a later part
  of a write group backs the round off too, and that changeset goes again as it is; the
  pull still comes in meanwhile, and a resend's commit found in it is settled as a replay;
- retry, offline: the round backs off (30 s, 2 min, 5 min, 15 min).

Pull. Changesets apply in seq order; a grouped one waits for its last part (24 h at
most). Content is downloaded and verified first (files to a temp file in their own
folder), then everything lands in one ``db.write_txn``, file moves included, so a writer
in any process sees all of it or none. Each item lands in its own savepoint with its file
moves journalled: one that fails rolls back alone, files too, and a transaction that
rolls back puts every file back. Per item:

- clean (or absent) here: theirs wins;
- a doc edited here: three-way JSON merge (:mod:`json_merge`) against the version mine is
  an edit of (kept here, else fetched; never merged without it); clean merges stay dirty
  and push as "Merged with Ana's change", true conflicts keep theirs and file mine in
  ``space_conflicts`` (sealed);
- a file edited here: theirs wins, mine is kept as a sealed side copy under ``conflicts/``;
- a remote delete of something edited here (or kept as mine in an unresolved conflict),
  or an edit of something deleted here: a conflict, never a silent loss;
- workflows also keep "Your copy before sync" in their History.

An item that can't be applied (a bad download, a file held open) is set aside in
``space_incoming_blocked`` and retried alone at its current version; the cursor moves on.
A pull from below what the server keeps lists every item and reconciles (resync), and so
does a plugin's first round after it moved from the provider's older sync (its rows carry
that sync's revisions). After a pull changed a plugin's data, its backend's
``api.data.on_change`` callbacks hear the keys. Nothing moves while the space is reset,
lost, unavailable or paused.

Folders a plugin keeps itself (``team_folders``, :mod:`team_folders`) are items of kind
``tree``, one per file, grouped in units. A unit quiet for 3 s goes out as one changeset,
its files copied once into core's outbox and streamed from there (never split: a very big
unit goes as parts of one group); its first change here takes the unit's lock. A pull
applies each unit whole and in seq order, with the origin's mtimes (a unit one of whose
files didn't arrive waits whole); a unit changed here is never overwritten: it is held as a
conflict (Keep mine, Take theirs, Keep both) unless its files merge (``merge`` globs). A
``reload`` plugin whose panel is on screen gets its incoming changes only on "Update now",
once its panel is hidden, or before an agent's call; meanwhile the units they touch are
read-only. After a pull changed its files a ``reload`` plugin restarts (every process that
loaded it, through its reload epoch) and ``notify`` panels hear it.

Callers hold the space's round lock (:func:`backend.uefn_plugins.sync_scheduler._round_lock`);
the functions here take it themselves when it isn't held on this thread.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import random
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from backend.store import db
from backend.store.repos import folder_files as ff
from backend.store.repos import plugin_data as repo
from backend.store.repos import space_vcs
from backend.uefn_plugins import data_crypto, json_merge, scopes
from backend.uefn_plugins import team_folders as tf
from backend.uefn_plugins.ledger import (
    Behind,
    Committed,
    Conflict,
    Denied,
    Done,
    Error,
    Granted,
    Heads,
    Held,
    History,
    Importing,
    IncomingChangeset,
    IncomingItem,
    ItemId,
    LockInfo,
    LockUnit,
    Locked,
    Missing,
    Offline,
    OutgoingChangeset,
    OutgoingItem,
    Paused,
    PullPage,
    Refused,
    Replay,
    Retry,
    Roster,
    failed,
    person,
    reason_of,
    unit_of,
)

_log = logging.getLogger(__name__)

WORKFLOWS = "ducky.automations"  # owned.DOC_PLUGIN
FOLDERS_KEY = "_folders"  # owned.FOLDERS_KEY: concurrent folder adds keep both
KINDS = ("doc", "asset")  # api.data items; folder files are TREE items (see "folders" below)
TREE = "tree"
SWITCH_PULL_S = 600.0  # a switch to a team gets its folder copy for at most this long
LEAVE_SEND_S = 30.0  # a switch away sends the old copy's changed units for at most this long
CALL_APPLY_WAIT_S = 60.0  # an agent's call waits this long for its plugin's waiting changes
PULL_LIMIT = 50
MAX_PAGES = 20
MAX_ITEMS = 400  # per commit call; a bigger group goes as parts
CONFLICT_MOVES = 3
MOVES_BACKOFF_S = (30.0, 120.0)
GROUP_WAIT_S = 24 * 3600.0
FETCH_WORKERS = 4
LOCK_RENEW_S = 60.0
AUTO_RELEASE_S = 30.0  # an auto lock frees this long after its last change went out
TAB_CLOSED_RELEASE_S = 10.0  # the plugin's tab closed this long ago: its auto locks go
BEAT_EVERY_S = 60.0
BEAT_MIN_S = 10.0  # presence goes out at most this often (the server takes 6 a minute)
MODES = ("auto", "manual")
RESOLVE_CHOICES = ("mine", "theirs")
CHANGED_WHILE_SENDING = "This PC's copy changed while it was being sent; it goes again."
SYNC_PAUSED = "Team sync paused"
# Space states (the sync row) in which nothing moves: the person chooses first (a reset),
# access is gone or unsure, or the space is read-only.
HELD_STATES = {
    "reset": "This space's data was reset on the server. Choose Upload my copy or Clear my copy first.",
    "lost": "You no longer have access to this space.",
    "unavailable": "This space isn't available right now.",
    "paused": "This space is paused. Read-only.",
}


@dataclass(frozen=True)
class Ctx:
    account: str
    space: str
    plugin: str
    backend: Any

    @property
    def scope(self) -> dict[str, Any]:
        return {"account": self.account, "id": self.space, "kind": "shared"}


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Backends never raise; one that does is an error answer, never the round's crash."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _log.warning("ledger call %s raised", getattr(fn, "__name__", fn), exc_info=True)
        return Error(f"{type(exc).__name__}: {exc}")


def _lock(account: str, space: str, wait: float) -> Any:
    from backend.uefn_plugins.sync_scheduler import _round_lock

    return _round_lock(account, space, wait)


def device_id() -> str:
    return space_vcs.device_id()


def _sha(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def _asset_path(ctx: Ctx, key: str) -> Path:
    return scopes.asset_file(ctx.scope, ctx.plugin, key)


def _conflicts_dir(ctx: Ctx) -> Path:
    return scopes.plugin_dir(ctx.scope, ctx.plugin) / "conflicts"


def _unit_name(unit: str) -> str:
    return unit.split(":", 1)[1] if unit.startswith(("doc:", "asset:")) else unit


_LABELS: dict[tuple[str, str, str, str], tuple[str, str]] = {}  # (account, space, plugin, key) → (sha256, label)
_LABELS_MAX = 2000


def unit_label(account: str, space: str, plugin: str, unit: str) -> str:
    """What people see a unit called (presence, the drawer): a doc's own name (its
    ``name``, ``title`` or ``label`` field, so "Untitled island 1" rather than
    ``project.mvbtpazj``), a file's name, else the key. Cached per version of the doc."""
    kind, _, key = unit.partition(":")
    if kind == "asset":
        return key.rsplit("/", 1)[-1] or key
    if kind != "doc" or not key:
        return _unit_name(unit)
    row = repo.get(account, space, plugin, "doc", key)
    if not row or row["deleted"] or row["value"] is None:
        return key
    hit = _LABELS.get((account, space, plugin, key))
    if hit and hit[0] == row["sha256"]:
        return hit[1]
    try:
        label = scopes.item_label(json.loads(data_crypto.open_text(row["value"], account)), key)
    except Exception:  # noqa: BLE001 — a doc that can't be read here is named by its key
        label = key
    if len(_LABELS) >= _LABELS_MAX:
        _LABELS.clear()
    _LABELS[(account, space, plugin, key)] = (str(row["sha256"]), label)
    return label


def _seen_keys(plugin: str, keys: Any) -> list[str]:
    """The doc keys and file paths a pull changed that the plugin reads: a team copy's items
    under a ``data_local`` prefix never are (it reads this PC's own instead)."""
    return [str(k) for k in keys or [] if not scopes.is_local_key(plugin, str(k))]


def _row_of_unit(unit: str) -> tuple[str, str] | None:
    kind, _, key = unit.partition(":")
    return (kind, key) if kind in KINDS and key else None


def _who(*names: Any) -> str:
    """:func:`ledger.person` at the start of a sentence."""
    return person(*names, fallback="A teammate")


_ME = {"at": float("-inf"), "id": ""}
ME_TTL_S = 30.0


def my_user_id() -> str:
    """The signed-in user as the server names lock holders and roster rows ('' signed out)."""
    now = time.monotonic()
    if now - _ME["at"] < ME_TTL_S:
        return _ME["id"]
    try:
        from frontend.duckyos_account import get_status

        uid = str(get_status().get("user_id") or "")
    except Exception:  # noqa: BLE001 — a name still shows
        uid = ""
    _ME.update(at=now, id=uid)
    return uid


def other_pc(account: str, space: str, user: Any, device: Any) -> str:
    """The PC's name when ``user`` is this account on another PC ('' otherwise). A lock
    belongs to one PC: the same account on another PC is locked out here all the same."""
    me = my_user_id()
    device = str(device or "")
    if not me or str(user or "") != me or not device or device == device_id():
        return ""
    return device_names(account, space).get(device) or "another PC"


def holder(account: str, space: str, user: Any, name: Any, device: Any) -> tuple[str, str]:
    """``(who, pc)`` of a lock's holder for every message: ``("you on MainPC", "MainPC")``
    for this account on another PC, else ``(the person's name, "")``."""
    pc = other_pc(account, space, user, device)
    return (f"you on {pc}", pc) if pc else (person(name, user), "")


def author(account: str, space: str, by: Any, device: Any = "", actor: Any = "user", ducky: Any = "") -> str:
    """Who made a change, for conflicts, Update now and merges. An AI's change always says
    AI, even under the person's own account: "AI (Ana's ducky Builder)", "AI, for Ana";
    this account on another PC is "you on MainPC"."""
    pc = other_pc(account, space, by, device)
    if str(actor or "") != "agent":
        return f"you on {pc}" if pc else str(by or "")  # a plain id stays an id: the reader names it
    if ducky:
        return f"AI (your ducky {ducky} on {pc})" if pc else f"AI ({person(by)}'s ducky {ducky})"
    return f"AI, for {'you on ' + pc if pc else person(by)}"


def lock_ducky(account: str, space: str, lk: Mapping[str, Any]) -> str | None:
    """The ducky behind a cached lock row ('' for an AI with no name), None for a person.
    A lock carries no actor on the wire: its holder PC's presence (or, for this PC's own,
    the activity here) says who changed the unit, so a ducky's lock never reads as its
    person's alone."""
    plugin, unit, name = str(lk.get("plugin_id") or ""), str(lk.get("unit") or ""), str(lk.get("name") or "")
    try:
        if lk.get("mine"):
            for a in space_vcs.activity(account, space):
                if a["plugin_id"] == plugin and a["unit"] == unit and a["actor"] == "agent":
                    return str(a["ducky"] or "")
            return None
        roster = space_vcs.presence_get(account, space)["roster"]
        people = roster.get("people") if isinstance(roster, Mapping) else None
        for p in people if isinstance(people, list) else []:
            if not isinstance(p, Mapping) or str(p.get("user") or "") != str(lk.get("holder_user") or "") \
                    or str(p.get("device") or "") != str(lk.get("holder_device") or ""):
                continue
            plugins = p.get("plugins") if isinstance(p.get("plugins"), Mapping) else {}
            here = plugins.get(plugin) if isinstance(plugins.get(plugin), Mapping) else {}
            for e in here.get("editing") or []:
                if isinstance(e, Mapping) and e.get("actor") == "agent" and str(e.get("unitName") or "") in (
                        name, unit.split(":", 1)[-1]):
                    return str(e.get("ducky") or "")
    except Exception:  # noqa: BLE001 — attribution never fails a lock
        return None
    return None


def holder_of(account: str, space: str, lk: Mapping[str, Any]) -> tuple[str, str]:
    """:func:`holder` of a cached lock row; a ducky's lock says so, like :func:`author`:
    "AI (Ana's ducky Builder)", "AI (your ducky Builder on MainPC)"."""
    who, pc = holder(account, space, lk.get("holder_user"), lk.get("holder_name"), lk.get("holder_device"))
    ducky = lock_ducky(account, space, lk)
    if ducky is None:
        return who, pc
    if lk.get("mine") or pc:
        on = f" on {pc}" if pc else ""
        return (f"AI (your ducky {ducky}{on})" if ducky else f"AI, for you{on}"), pc
    name = person(lk.get("holder_name"), lk.get("holder_user"))
    return (f"AI ({name}'s ducky {ducky})" if ducky else f"AI, for {name}"), pc


# --------------------------------------------------------------------------- push


def _snapshot(r: Mapping[str, Any]) -> dict[str, Any]:
    """A row as it goes out: what the compare-and-set after the commit checks."""
    return {"kind": r["kind"], "key": r["key"], "unit": unit_of(r["kind"], r["key"]),
            "op": "del" if int(r["deleted"]) else "put", "sha256": r["sha256"], "deleted": int(r["deleted"]),
            "updated": float(r["updated"])}


def _build(ctx: Ctx, *, message: str = "", now: float) -> int:
    """New changesets from rows ready to go out, written before any upload. Returns how many."""
    rows = [r for r in space_vcs.outgoing_rows(ctx.account, ctx.space, ctx.plugin, now) if r["kind"] in KINDS]
    if not rows:
        return 0
    busy = space_vcs.cs_items_open(ctx.account, ctx.space, ctx.plugin) | _waiting(ctx)
    groups = space_vcs.groups(r["group_id"] for r in rows)
    # (group id, actor, ducky, author, author_pc): a write group says who made it; a row of
    # no group goes by who changed it here last, so an AI's write outside a group is still
    # the AI's; a groupmate's change this PC sends as its live group's leader names them.
    batches: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    last_by = _actors(ctx, now)
    from backend.uefn_plugins import live

    hold = live.hold_since(ctx.account, ctx.space, ctx.plugin)
    for r in rows:
        if (r["kind"], r["key"]) in busy:
            continue
        if (hold is not None and float(r["updated"]) >= hold and now - float(r["updated"]) < live.HOLD_MAX_S
                and live.carried(r["kind"], int(r["size"] or 0), deleted=bool(int(r["deleted"] or 0)))):
            continue  # the live group's leader sends it for this PC (it can't send one too big to carry)
        theirs = live.relayed(ctx.account, ctx.space, ctx.plugin, r["kind"], r["key"], str(r["sha256"] or ""))
        if theirs is not None:
            batches.setdefault(("", theirs["actor"], theirs["ducky"], theirs["author"], theirs["author_pc"]), []).append(r)
            continue
        held = space_vcs.foreign_lock(ctx.account, ctx.space, ctx.plugin, unit_of(r["kind"], r["key"]), now=now)
        if held:
            repo.set_state(ctx.account, ctx.space, ctx.plugin, r["kind"], r["key"], "locked_out",
                           f"Locked by {holder_of(ctx.account, ctx.space, held)[0]}")
            continue
        info = groups.get(r["group_id"]) if r["group_id"] else None
        if info and info["open"]:
            continue  # the call that writes this group is still running
        if info:
            batches.setdefault((r["group_id"], str(info.get("actor") or "user"), str(info.get("ducky") or ""), "", ""),
                               []).append(r)
        else:
            batches.setdefault(("", *last_by.get(unit_of(r["kind"], r["key"]), ("user", "")), "", ""), []).append(r)
    seq = int(space_vcs.head_get(ctx.account, ctx.space, ctx.plugin)["seq"])
    made = 0
    for (gid, actor, ducky, author, author_pc), batch in batches.items():
        info = groups.get(gid) or {}
        parts = [batch[i:i + MAX_ITEMS] for i in range(0, len(batch), MAX_ITEMS)]
        for n, part in enumerate(parts, 1):
            # A write group's parts share its id and the final one says it is the last. Rows
            # of no group carry no promise to land together: each slice is a changeset of its
            # own, never a group made up here that a rebuild would never close.
            space_vcs.cs_add(ctx.account, ctx.space, ctx.plugin, uuid.uuid4().hex, base_seq=seq,
                             items=[_snapshot(r) for r in part], group_id=gid, part=n if gid else 1,
                             last=n == len(parts) or not gid, message=message or str(info.get("message") or ""),
                             actor=actor, ducky=ducky, author=author, author_pc=author_pc)
            made += 1
    return made


def _actors(ctx: Ctx, now: float) -> dict[str, tuple[str, str]]:
    """Unit → (actor, ducky) of who changed it here last (the activity this PC noted)."""
    out: dict[str, tuple[str, str]] = {}
    for act in space_vcs.activity(ctx.account, ctx.space, now=now):
        if act["plugin_id"] == ctx.plugin and act["unit"] not in out:
            out[act["unit"]] = ("agent" if act["actor"] == "agent" else "user", str(act["ducky"] or ""))
    return out


def _waiting(ctx: Ctx) -> set[ItemId]:
    """Items whose newer version waits to be applied here (set aside): mine can't go out on
    top of a version this PC hasn't got, and would hold up the rows going with it."""
    return {(b["kind"], b["key"]) for b in space_vcs.blocked(ctx.account, ctx.space, ctx.plugin)}


def _outgoing(ctx: Ctx, r: Mapping[str, Any], failures: dict[ItemId, str]) -> OutgoingItem | None:
    """The row as an item to send, its content plaintext. A row that can't be read here
    backs off (and is blocked after a few tries); None then."""
    kind, key = r["kind"], r["key"]
    unit = unit_of(kind, key)
    base = int(r["rev"])
    if int(r["deleted"]):
        return OutgoingItem(kind=kind, key=key, unit=unit, op="del", base_seq=base)
    try:
        if kind == "doc":
            if r["value"] is None:
                raise ValueError("the doc has no content")
            raw = data_crypto.open_text(r["value"], ctx.account).encode("utf-8")
            return OutgoingItem(kind=kind, key=key, unit=unit, op="put", base_seq=base, sha256=_sha(raw),
                                size=len(raw), data=raw)
        path = _asset_path(ctx, key)
        if not path.is_file():
            raise FileNotFoundError("This PC's copy of the file is missing.")
    except data_crypto.Locked:
        raise
    except Exception as exc:  # noqa: BLE001 — one unreadable row never holds up the rest
        repo.note_attempt(ctx.account, ctx.space, ctx.plugin, kind, key, f"{type(exc).__name__}: {exc}")
        return None
    account = ctx.account
    sha, size = str(r["sha256"]), int(r["size"])

    def read() -> bytes:
        # The backend reads when it uploads, maybe more than once and minutes later: a file
        # rewritten meanwhile would go out under the sha256 and size of the row as it was.
        try:
            raw = data_crypto.open_bytes(path.read_bytes(), account)
        except Exception as exc:  # noqa: BLE001 — recorded against the row, re-raised to the backend
            failures[(kind, key)] = f"{type(exc).__name__}: {exc}"
            raise
        if len(raw) != size or _sha(raw) != sha:
            failures[(kind, key)] = CHANGED_WHILE_SENDING
            raise ValueError(CHANGED_WHILE_SENDING)
        return raw

    return OutgoingItem(kind=kind, key=key, unit=unit, op="put", base_seq=base, sha256=sha, size=size, reader=read)


@dataclass
class _Out:
    kind: str  # committed | dropped | conflict | locked | refused | failed | missing | stop
    seq: int = 0
    state: str = ""
    error: str = ""
    sent: list[dict[str, Any]] | None = None  # failed, missing: the rows (files) this try sent


def _later_part(cs: Mapping[str, Any]) -> bool:
    """A part of a write group after its first: an earlier part may have landed already."""
    return bool(cs["group_id"]) and int(cs["part"]) > 1


def _push_cs(ctx: Ctx, cs: dict[str, Any], *, now: float, fresh: bool | None = None) -> _Out:
    """Send one changeset and settle its answer. ``fresh``: no try of it can have committed
    (it was pending when this round began on it; default: pending now). An error then is
    ``failed`` for the round to settle (:func:`_push_round`); on a resend, or on a later
    part of a write group, it backs the plugin off and the same changeset goes again, so a
    commit whose answer got lost is answered (replay) and a group closes."""
    if _is_tree_cs(cs):
        return _push_tree_cs(ctx, cs, now=now, fresh=fresh)
    first_try = cs["state"] == "pending"
    fresh = first_try if fresh is None else fresh
    items: list[OutgoingItem] = []
    sent: list[dict[str, Any]] = []
    bases: dict[str, tuple[str, str]] = {}
    failures: dict[ItemId, str] = {}
    waiting = _waiting(ctx)
    going: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []
    for snap in cs["items"]:
        r = space_vcs.row(None, ctx.account, ctx.space, ctx.plugin, str(snap.get("kind")), str(snap.get("key")))
        if (not r or not int(r["dirty"]) or int(r["sensitive"]) or r["sync_state"] in space_vcs.HELD_STATES
                or float(r["next_try"]) > now):
            continue  # settled meanwhile (pulled, dropped, held): it no longer goes with this changeset
        if (r["kind"], r["key"]) in waiting or (r["group_id"] and r["group_id"] != cs["group_id"]):
            # A newer version of it waits to be applied here, or another call (or a merge)
            # rewrote it since this changeset was made: it goes later, with that call's
            # changeset once the call returned, never half of that call in this one.
            later.append(r)
            continue
        going.append(r)
    if not going and not first_try:
        going = later  # sent before: the server's answer (a commit whose reply got lost) is still needed
    for r in going:
        item = _outgoing(ctx, r, failures)
        if item is None:
            continue
        items.append(item)
        sent.append(_snapshot(r))
        if r["kind"] == "doc" and not int(r["deleted"]):
            bases[r["key"]] = (str(r["sha256"]), r["value"])
    if not items:
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        return _Out("dropped")
    # Written before the upload. Once a try went out, what it sent stays the record: a
    # replay is checked against it, so an edit made after it is never taken as sent.
    if first_try:
        space_vcs.cs_set(ctx.account, ctx.space, cs["cs_id"], state="pushing", items=sent)
    first = sent if first_try else cs["items"]
    units = sorted({i.unit for i in items})
    fences = {lk["unit"]: int(lk["fence"]) for lk in space_vcs.locks(ctx.account, ctx.space, ctx.plugin, now=now)
              if lk["mine"] and lk["unit"] in units}
    head = space_vcs.head_get(ctx.account, ctx.space, ctx.plugin)
    out = OutgoingChangeset(cs_id=cs["cs_id"], base_seq=int(head["seq"]), items=tuple(items), device=device_id(),
                            group=cs["group_id"], part=int(cs["part"]), last=bool(cs["last"]), message=cs["message"],
                            actor=cs["actor"], ducky=cs["ducky"], fences=fences, author=str(cs.get("author") or ""),
                            author_pc=str(cs.get("author_pc") or ""))
    res = _call(ctx.backend.push, ctx.space, ctx.plugin, out)
    if failures and not isinstance(res, (Committed, Replay)):
        went = {(str(s_["kind"]), str(s_["key"])): s_ for s_ in sent}
        for (kind, key), err in failures.items():
            if err == CHANGED_WHILE_SENDING and _rewritten(ctx, went.get((kind, key))):
                continue  # written again while it was sent: it goes again with what it is now
            repo.note_attempt(ctx.account, ctx.space, ctx.plugin, kind, key, err)
        return _Out("dropped")  # the changeset goes again without the rows that couldn't be read
    if isinstance(res, (Committed, Replay)):
        snaps = sent if isinstance(res, Committed) else first
        if isinstance(res, Replay):  # an earlier try committed: its content is known only where nothing changed
            sent_sha = {str(s["key"]): str(s["sha256"]) for s in first if s["kind"] == "doc"}
            bases = {k: v for k, v in bases.items() if sent_sha.get(k) == v[0]}
        space_vcs.mark_committed(ctx.account, ctx.space, ctx.plugin, res.seq, snaps, bases=bases, units=units)
        space_vcs.cs_set(ctx.account, ctx.space, cs["cs_id"], state="committed", seq=res.seq)
        space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, moves=0)
        return _Out("committed", seq=res.seq)
    if isinstance(res, Conflict):
        _gone_for_good(ctx, res)
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        return _Out("conflict")
    if isinstance(res, Locked):
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        for lk in res.units:
            _cache_lock(ctx, lk, now)
            row = _row_of_unit(lk.unit)
            if row:
                repo.set_state(ctx.account, ctx.space, ctx.plugin, *row, "locked_out",
                               f"Locked by {holder(ctx.account, ctx.space, lk.user, lk.user_name, lk.device)[0]}")
        return _Out("locked")
    if isinstance(res, Refused):
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        for it in res.items:
            repo.set_state(ctx.account, ctx.space, ctx.plugin, it.kind, it.key, "refused", it.reason)
        return _Out("refused")
    if isinstance(res, Missing):
        return _Out("missing", error=reason_of(res), sent=sent)
    if isinstance(res, Error) and fresh and not _later_part(cs):
        return _Out("failed", error=reason_of(res), sent=sent)
    return _stop(ctx, res, now=now)


def _group_unsent(ctx: Ctx, cs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The other parts of ``cs``'s write group not sent yet."""
    if not cs["group_id"]:
        return []
    return [o for o in space_vcs.cs_open(ctx.account, ctx.space, ctx.plugin)
            if o["group_id"] == cs["group_id"] and o["cs_id"] != cs["cs_id"] and o["state"] == "pending"]


def _splittable(cs: Mapping[str, Any], sent: list[dict[str, Any]] | None) -> bool:
    """Rows of no write group, more than one: they carry no promise to land together."""
    return not cs["group_id"] and not _is_tree_cs(cs) and len(sent if sent is not None else cs["items"]) > 1


def _split(ctx: Ctx, cs: dict[str, Any], sent: list[dict[str, Any]] | None) -> frozenset[str]:
    """A failed changeset of rows of no write group goes again at once as two halves, with no
    try counted: halving finds the row the server won't take in about 2·log2(n) pushes while
    the other rows land in a few commits. Returns the halves' ids."""
    a = (ctx.account, ctx.space, ctx.plugin)
    space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
    now = time.time()
    rows = []
    for s in (sent if sent is not None else cs["items"]):
        r = space_vcs.row(None, *a, str(s["kind"]), str(s["key"]))
        if (r and int(r["dirty"]) and not int(r["sensitive"]) and not r["group_id"]
                and r["sync_state"] not in space_vcs.HELD_STATES and float(r["next_try"]) <= now):
            rows.append(r)
    seq = int(space_vcs.head_get(*a)["seq"])
    half = (len(rows) + 1) // 2
    made = []
    for part in (rows[:half], rows[half:]):
        if part:
            made.append(uuid.uuid4().hex)
            space_vcs.cs_add(*a, made[-1], base_seq=seq, items=[_snapshot(r) for r in part], message=cs["message"],
                             actor=cs["actor"], ducky=cs["ducky"])
    return frozenset(made)


def _failed(ctx: Ctx, cs: dict[str, Any], reason: str, *, snaps: Iterable[Mapping[str, Any]] | None = None) -> None:
    """The server took none of ``cs`` and said why while it took other changesets: the error
    is this changeset's own. It goes, and its rows count a try
    (:func:`plugin_data.note_attempts`; a folder unit's, :func:`space_vcs.unit_try_note`)
    and go again after the backoff, ``blocked`` with the reason after the last. Parts of its
    write group not sent yet go and count with it, so a group never lands in halves.
    ``snaps``: the rows this try sent (else what the changeset holds)."""
    a = (ctx.account, ctx.space, ctx.plugin)
    group = [cs, *_group_unsent(ctx, cs)]
    for o in group:
        space_vcs.cs_delete(ctx.account, ctx.space, o["cs_id"])
    _log.info("a changeset of %s in %s failed (%s); its rows wait", ctx.plugin, ctx.space, reason)
    if _is_tree_cs(cs):
        for o in group:
            tf.drop_snapshots(*a, o["cs_id"])
        named = _units_of(s for o in group for s in o["items"] if s.get("kind") == TREE)
        got = space_vcs.unit_try_note(*a, [_unit_key(fid, unit) for fid, unit in named.values()], reason,
                                      block_after=repo.BLOCK_AFTER)
        if got["blocked"]:
            for fid, unit in named.values():
                ff.unit_set_state(*a, fid, unit, "blocked", reason)
        return
    mine = list(cs["items"] if snaps is None else snaps)
    rows = {(str(s["kind"]), str(s["key"])) for s in [*mine, *(s for o in group[1:] for s in o["items"])]
            if not _rewritten(ctx, s)}  # written again since: it goes as it is now, with no try counted
    if rows:
        repo.note_attempts(*a, sorted(rows), reason)


def _rewritten(ctx: Ctx, snap: Mapping[str, Any] | None) -> bool:
    """The row is no longer what went out (a newer write landed since)."""
    if snap is None:
        return False
    r = space_vcs.row(None, ctx.account, ctx.space, ctx.plugin, str(snap["kind"]), str(snap["key"]))
    return r is None or r["sha256"] != snap["sha256"] or float(r["updated"]) != float(snap["updated"])


def _gone_for_good(ctx: Ctx, conflict: Conflict) -> None:
    """Items the server no longer has at all (purged): an edit here is an edit against a
    delete (Restore mine makes it again); a delete here is simply settled."""
    purged = [c for c in conflict.items if c.deleted and not c.seq]
    if not purged:
        return
    conn = db.connect()
    with db.write_txn(conn):
        for c in purged:
            cur = space_vcs.row(conn, ctx.account, ctx.space, ctx.plugin, c.kind, c.key)
            if cur is None or not int(cur["dirty"]):
                continue
            if int(cur["deleted"]):
                space_vcs.delete_row(conn, ctx.account, ctx.space, ctx.plugin, c.kind, c.key)
                continue
            space_vcs.conflict_put(conn, ctx.account, ctx.space, ctx.plugin, c.kind, c.key,
                                   unit=unit_of(c.kind, c.key), reason="deleted", mine_value=cur["value"],
                                   mine_sha=cur["sha256"], mine_size=int(cur["size"]), base_seq=int(cur["rev"]),
                                   theirs_by=c.by, theirs_at=c.at, theirs_deleted=True)
            space_vcs.set_row_state(conn, ctx.account, ctx.space, ctx.plugin, c.kind, c.key, "conflict",
                                    "This was deleted for good; you changed it.", rev=0)
            space_vcs.base_delete(conn, ctx.account, ctx.space, ctx.plugin, c.key)


def _stop(ctx: Ctx, res: Any, *, now: float) -> _Out:
    """A round can't go on: record why and when to try again."""
    reason = reason_of(res)
    if isinstance(res, Retry):
        space_vcs.backoff(ctx.account, ctx.space, ctx.plugin, "retry", reason, after=res.after, now=now)
        return _Out("stop", state="retry", error=reason)
    if isinstance(res, Importing):
        space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, state="importing", error=reason)
        return _Out("stop", state="importing", error=reason)
    if isinstance(res, Paused):
        space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, state="paused", error="")
        return _Out("stop", state="paused", error="")
    state = "offline" if isinstance(res, Offline) else "error"
    space_vcs.backoff(ctx.account, ctx.space, ctx.plugin, state, reason, now=now)
    return _Out("stop", state=state, error=reason)


@dataclass
class _Errors:
    """What a push round learnt from error answers to fresh changesets (the server took none
    of them). ``aside``: changesets that stepped aside, pending again (cs_id → (reason, rows
    sent)); ``held``: those and their write groups' parts not sent yet, skipped for the rest
    of the round; ``answered``: the server answered another changeset on its merits
    (committed, conflict, locked, refused), so an error is the changeset's own; ``fails``:
    failures since the last commit; ``halves``: what the last split made; ``reason``: the
    last error."""

    aside: dict[str, tuple[str, list[dict[str, Any]] | None]] = field(default_factory=dict)
    held: set[str] = field(default_factory=set)
    answered: bool = False
    fails: int = 0
    halves: frozenset[str] = frozenset()
    reason: str = ""


def _step_aside(ctx: Ctx, errs: _Errors, cs: dict[str, Any], out: _Out) -> bool:
    """A fresh changeset failed (an error, or an upload missing twice). Rows of no write
    group go again at once as two halves (:func:`_split`); anything else steps aside,
    pending again with its group's unsent parts, until the round ends. Returns True when
    the round ends here: a second failure since the last commit, unless it is a half of
    the failure just before (one half of a split is expected to fail)."""
    errs.fails += 1
    errs.reason = out.error
    cut = errs.fails >= 2 and cs["cs_id"] not in errs.halves
    current = space_vcs.cs_get(ctx.account, ctx.space, cs["cs_id"]) or cs
    if _splittable(current, out.sent):
        errs.halves = _split(ctx, current, out.sent)
        return cut
    space_vcs.cs_set(ctx.account, ctx.space, cs["cs_id"], state="pending")
    errs.aside[cs["cs_id"]] = (out.error, out.sent)
    errs.held |= {cs["cs_id"], *(o["cs_id"] for o in _group_unsent(ctx, current))}
    errs.halves = frozenset()
    return cut


def _push_round(ctx: Ctx, *, message: str = "", now: float, pull: bool = True,
                quiet_s: float = tf.QUIET_S) -> dict[str, Any]:
    """Send what is ready. ``pull`` False (the exit flush): a conflict ends the round instead
    of pulling, so nothing is applied while the app is closing; the rows go next time.
    ``quiet_s``: how long a folder unit must be unchanged to go (0: every changed unit
    without a save in progress, as a switch away sends them).

    An error to a fresh changeset (:func:`_step_aside`) is that changeset's own only when
    the server answered another one in the round: then its rows count a try
    (:func:`_failed`). When it answered none, it is taken to refuse the plugin (a rate
    limit, a broken server): nothing counts a try, the changesets stay pending and the
    round backs off with the error, as it does after two failures in a row."""
    errs = _Errors()
    try:
        out = _push_loop(ctx, errs, message=message, now=now, pull=pull, quiet_s=quiet_s)
    finally:
        if errs.answered:
            for cs_id, (reason, sent) in errs.aside.items():
                cs = space_vcs.cs_get(ctx.account, ctx.space, cs_id)
                if cs is not None and cs["state"] == "pending":
                    _failed(ctx, cs, reason, snaps=sent)
    if out["state"] == "ok" and errs.reason and not errs.answered:
        stop = _stop(ctx, Error(errs.reason), now=now)
        out = {**out, "state": stop.state, "error": stop.error}
    return out


def _push_loop(ctx: Ctx, errs: _Errors, *, message: str, now: float, pull: bool, quiet_s: float) -> dict[str, Any]:
    from backend.uefn_plugins import live

    pushed = tree = 0
    missing: dict[str, int] = {}
    fresh: set[str] = set()  # pending when this round began on them: no try of them can have committed
    folders = bool(tf.plugin_folders(ctx.plugin))
    if folders:  # copies made to go out by changesets no longer waiting (a crash's leftovers) go
        tf.prune_snapshots(ctx.account, ctx.space, ctx.plugin,
                           {cs["cs_id"] for cs in space_vcs.cs_open(ctx.account, ctx.space, ctx.plugin)})
    for _ in range(50):
        _build(ctx, message=message, now=now)
        if folders:
            _build_tree(ctx, now=now, quiet_s=quiet_s)
        pending = [cs for cs in space_vcs.cs_open(ctx.account, ctx.space, ctx.plugin) if cs["cs_id"] not in errs.held]
        if not pending:
            break
        again = False
        for cs in pending:
            if cs["state"] == "pending":
                fresh.add(cs["cs_id"])
            out = _push_cs(ctx, cs, now=now, fresh=cs["cs_id"] in fresh)
            if out.kind == "committed":
                pushed += 1
                errs.answered, errs.fails, errs.halves = True, 0, frozenset()
                live.committed(ctx.account, ctx.space, ctx.plugin, throttle=True)  # the group pulls it now
                continue
            if out.kind in ("dropped", "locked", "refused"):
                errs.answered = errs.answered or out.kind != "dropped"
                fresh.discard(cs["cs_id"])  # one kept (an unreadable row left out) goes again as a resend
                again = True
                continue
            if out.kind == "missing":
                missing[cs["cs_id"]] = missing.get(cs["cs_id"], 0) + 1
                again = True
                if missing[cs["cs_id"]] == 1:
                    break  # the backend sends what went missing on the next try
                if cs["cs_id"] not in fresh or _later_part(cs):  # still missing: as an error to a resend
                    stop = _stop(ctx, Error(out.error), now=now)
                    return {"state": stop.state, "error": stop.error, "pushed": pushed, "tree": tree}
                out = _Out("failed", error=out.error, sent=out.sent)
            if out.kind == "failed":
                if _step_aside(ctx, errs, cs, out):
                    stop = _stop(ctx, Error(out.error), now=now)
                    return {"state": stop.state, "error": stop.error, "pushed": pushed, "tree": tree}
                again = True
                break
            if out.kind == "conflict":
                errs.answered = True
                if not pull:
                    return {"state": "ok", "error": "", "pushed": pushed, "tree": tree}
                moves = int(space_vcs.head_get(ctx.account, ctx.space, ctx.plugin)["moves"]) + 1
                space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, moves=moves)
                if moves >= CONFLICT_MOVES:
                    space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, moves=0)
                    space_vcs.backoff(ctx.account, ctx.space, ctx.plugin, "ok", "",
                                      after=random.uniform(*MOVES_BACKOFF_S), now=now)
                    return {"state": "retry", "error": "Others keep changing this; trying again soon.",
                            "pushed": pushed, "tree": tree}
                pulled = _pull_round(ctx, now=now)
                tree += int(pulled.get("tree") or 0)
                if pulled["state"] != "ok":
                    return {**pulled, "pushed": pushed, "tree": tree}
                if pulled.get("queued"):  # the newer changes wait behind Update now: so does this
                    return {"state": "ok", "error": "", "pushed": pushed, "tree": tree}
                again = True
                break
            return {"state": out.state, "error": out.error, "pushed": pushed, "tree": tree}
        if not again:
            break
    return {"state": "ok", "error": "", "pushed": pushed, "tree": tree}


# --------------------------------------------------------------------------- pull


def _groups_open(buffer: list[IncomingChangeset], now: float) -> bool:
    """A group in ``buffer`` still waits for its last part (24 h at most)."""
    started: dict[str, float] = {}
    done: set[str] = set()
    for cs in buffer:
        if not cs.group:
            continue
        started.setdefault(cs.group, cs.at or now)
        if cs.last:
            done.add(cs.group)
    return any(g not in done and now - at < GROUP_WAIT_S for g, at in started.items())


def _download(ctx: Ctx, items: list[IncomingItem]) -> dict[ItemId, Any]:
    """Each put's content (4 at a time). An item that already carries it is used as is."""
    out: dict[ItemId, Any] = {it.id: it for it in items if it.data is not None or it.path}
    todo = [it for it in items if it.id not in out]
    if not todo:
        return out

    def one(it: IncomingItem) -> tuple[ItemId, Any]:
        return it.id, _call(ctx.backend.fetch, ctx.space, ctx.plugin, it.kind, it.key, it.seq or None)

    if len(todo) == 1:
        vid, res = one(todo[0])
        out[vid] = res
        return out
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="space-fetch") as pool:
        for vid, res in pool.map(one, todo):
            out[vid] = res
    return out


def _drop_temp(got: Any) -> None:
    path = getattr(got, "path", "")
    if path:
        Path(path).unlink(missing_ok=True)


@dataclass
class _Prep:
    """An incoming put made ready before the transaction: a doc parsed and sealed, a file
    sealed into a temp file next to its target. ``error``: why it can't be applied."""

    sha256: str = ""
    size: int = 0
    raw: bytes = b""
    sealed: str | None = None
    temp: Path | None = None
    target: Path | None = None
    error: str = ""


def _prepare(ctx: Ctx, it: IncomingItem, got: Any) -> _Prep:
    if failed(got) or not isinstance(got, IncomingItem):
        return _Prep(error=reason_of(got) or "The download failed.")
    if got.op == "del":
        return _Prep()
    try:
        raw = got.read()
        sha = _sha(raw)
        want = got.sha256 or it.sha256
        if want and want != sha:
            return _Prep(error="The download didn't match what was sent.")
        if it.kind == "doc":
            json.loads(raw)
            return _Prep(sha256=sha, size=len(raw), raw=raw, sealed=data_crypto.seal_text(raw.decode("utf-8"),
                                                                                         ctx.account))
        target = _asset_path(ctx, it.key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        temp.write_bytes(data_crypto.seal_bytes(raw, ctx.account))
        return _Prep(sha256=sha, size=len(raw), temp=temp, target=target)
    except data_crypto.Locked:
        raise
    except Exception as exc:  # noqa: BLE001 — set aside, retried alone
        return _Prep(error=f"{type(exc).__name__}: {exc}")
    finally:
        _drop_temp(got)


def _has_conflict(conn: Any, ctx: Ctx, kind: str, key: str) -> bool:
    return space_vcs.conflict_get(ctx.account, ctx.space, ctx.plugin, kind, key, conn=conn) is not None


def _own_echo(ctx: Ctx, it: IncomingItem, prep: Any) -> bool:
    """The incoming version is one this PC sent its live group earlier (:func:`live.own_echo`)."""
    try:
        from backend.uefn_plugins import live

        return live.own_echo(ctx.account, ctx.space, ctx.plugin, it.kind, it.key, str(prep.sha256 or ""))
    except Exception:  # noqa: BLE001 — unsure: the usual merge
        return False


def _holds_mine(c: Mapping[str, Any] | None) -> bool:
    """A conflict that keeps a version of mine (not a delete of mine)."""
    return bool(c) and not int(c["mine_deleted"]) and (c["mine_value"] is not None or bool(c["mine_path"]))


HELD_OPEN = "The file is open in another program here; it is tried again soon."
# A lock request that finds this PC behind on a unit tries its set-aside downloads again at
# once only this often; then they wait their backoff (space_vcs.blocked_put, up to an hour).
AUTO_RETRIES = 3


def _why(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return HELD_OPEN
    return f"{type(exc).__name__}: {exc}"


def _retry_soon(b: Mapping[str, Any]) -> bool:
    """A set-aside download a lock request may try again now: one that failed only a few
    times, or for a passing reason (a file held open). One failing the same way every time
    (a path too long, a bad copy) waits its turn and keeps its reason; it is never dropped,
    so a fix or an update still lands it. Before, every auto lock on its unit retried it."""
    # ponytail: tries since its last success stand in for "the same error N times"; the
    # table keeps only the last error.
    return int(b["attempts"]) < AUTO_RETRIES or str(b["error"]) == HELD_OPEN


class _Files:
    """File moves made inside a transaction, so a rollback puts the files back too. A move
    marked ``keep`` stays once the transaction commits; any other one was a file moved out
    of the way, removed then."""

    def __init__(self) -> None:
        self.moves: list[tuple[Path, Path, bool]] = []  # (from, to, keep)

    def move(self, src: Path, dst: Path, *, keep: bool) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        self.moves.append((src, dst, keep))

    def aside(self, path: Path) -> None:
        """``path`` out of the way of what replaces it (a file held open raises here, before
        anything changed)."""
        if path.exists():
            self.move(path, path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.old"), keep=False)

    def undo(self, mark: int = 0) -> None:
        """Every move since ``mark`` back, newest first."""
        while len(self.moves) > mark:
            src, dst, _ = self.moves.pop()
            try:
                os.replace(dst, src)
            except OSError:
                _log.warning("putting back %s failed", src, exc_info=True)

    def committed(self) -> None:
        for _, dst, keep in self.moves:
            if not keep:
                dst.unlink(missing_ok=True)
        self.moves = []


class _Apply:
    """One transaction's worth of incoming items. Each item lands in its own savepoint, its
    file moves journalled: an item that can't be applied (a file held open, a copy that
    won't open) rolls back alone, files included, and is set aside to be retried; a
    transaction that rolls back as a whole puts every file back. ``merge`` False: a doc
    edited here is never merged (a conflict unless both are the same). ``bases``: merge
    bases fetched for this apply, ``{(key, seq): plaintext}``."""

    def __init__(self, ctx: Ctx, conn: Any, *, force: bool, merge: bool = True,
                 bases: Mapping[tuple[str, int], bytes] | None = None) -> None:
        self.ctx, self.conn, self.force, self.merge = ctx, conn, force, merge
        self.bases = dict(bases or {})
        self.files = _Files()
        self.changed: list[str] = []
        self.blocked: set[str] = set()
        self.cleanup: list[Path] = []  # side copies replaced by a newer one: removed after the commit
        self.conflicts = 0
        self.merges = 0
        self._merge_groups: dict[str, str] = {}

    def _merge_group(self, by: str) -> str:
        gid = self._merge_groups.get(by)
        if gid is None:
            gid = self._merge_groups[by] = f"merge-{uuid.uuid4().hex}"
            space_vcs.group_add_closed(self.conn, gid, self.ctx.plugin,
                                       message=f"Merged with a change by {person(by)}" if by else "Merged with a change")
        return gid

    def one(self, it: IncomingItem, seq: int, by: str, at: float, prep: _Prep | None, *,
            action: Callable[[], None] | None = None) -> None:
        """:meth:`item` (or ``action``) in a savepoint. A failure rolls back just this item and
        sets it aside; a missing data key (``Locked``) stops the whole apply."""
        mark = (len(self.files.moves), len(self.changed), len(self.cleanup), self.conflicts, self.merges,
                dict(self._merge_groups))
        self.conn.execute("SAVEPOINT apply_item")
        try:
            (action or (lambda: self.item(it, seq, by, at, prep)))()
        except Exception as exc:
            self.conn.execute("ROLLBACK TO apply_item")
            self.conn.execute("RELEASE apply_item")
            self.files.undo(mark[0])
            del self.changed[mark[1]:]
            del self.cleanup[mark[2]:]
            self.conflicts, self.merges, self._merge_groups = mark[3], mark[4], mark[5]
            if isinstance(exc, data_crypto.Locked):
                raise
            _log.warning("%s %s couldn't be applied; set aside", it.kind, it.key, exc_info=True)
            unit = it.unit or unit_of(it.kind, it.key)
            space_vcs.blocked_put(self.conn, self.ctx.account, self.ctx.space, self.ctx.plugin, it.kind, it.key,
                                  unit=unit, seq=seq, error=_why(exc))
            self.blocked.add(unit)
            return
        self.conn.execute("RELEASE apply_item")

    def item(self, it: IncomingItem, seq: int, by: str, at: float, prep: _Prep | None) -> None:
        ctx, conn = self.ctx, self.conn
        a = (ctx.account, ctx.space, ctx.plugin, it.kind, it.key)
        cur = space_vcs.row(conn, *a)
        if cur is not None and int(cur["sensitive"]):
            return
        dirty = bool(cur and int(cur["dirty"]))
        if cur is not None and int(cur["rev"]) >= seq and (dirty or not self.force):
            space_vcs.blocked_delete(conn, *a)
            return  # this version or a newer one is already the base here
        if it.op == "del":
            self._delete(it, seq, by, at, cur)
            return
        if prep is None or prep.error:
            space_vcs.blocked_put(conn, *a, unit=it.unit or unit_of(it.kind, it.key), seq=seq,
                                  error=(prep.error if prep else "") or "The download failed.")
            self.blocked.add(it.unit or unit_of(it.kind, it.key))
            return
        space_vcs.blocked_delete(conn, *a)
        old = space_vcs.conflict_get(*a, conn=conn)
        if old is not None and old["reason"] == "deleted":
            space_vcs.conflict_delete(conn, *a)  # they made it again: the delete it was about is over
        if cur is None or not dirty or (not int(cur["deleted"]) and cur["sha256"] == prep.sha256):
            self._theirs(it, seq, prep, keep_conflict=cur is not None and _has_conflict(conn, ctx, it.kind, it.key))
            return
        if _own_echo(ctx, it, prep):
            # An older version of this PC's own edit, sent for it by its live group's leader:
            # the base moves on, and the newer edit made here since stays to go out.
            space_vcs.set_row_state(conn, *a, "", rev=seq)
            if it.kind == "doc":
                space_vcs.base_put(conn, ctx.account, ctx.space, ctx.plugin, it.key, seq, prep.sha256, prep.sealed)
            return
        if int(cur["deleted"]):
            # Deleted here, changed there: theirs is applied, the delete waits for the person.
            self._theirs(it, seq, prep, keep_conflict=True, error=f"{_who(by)} changed this; you deleted it.")
            space_vcs.conflict_put(conn, *a, unit=unit_of(it.kind, it.key), reason="mine_deleted", mine_deleted=True,
                                   base_seq=int(cur["rev"]), theirs_seq=seq, theirs_by=by, theirs_at=at)
            self.conflicts += 1
            return
        if it.kind == "doc":
            self._merge_doc(it, seq, by, at, cur, prep)
        else:
            self._keep_both_files(it, seq, by, at, cur, prep)

    def _theirs(self, it: IncomingItem, seq: int, prep: _Prep, *, keep_conflict: bool, error: str = "") -> None:
        ctx, conn = self.ctx, self.conn
        state = "conflict" if keep_conflict else ""
        if it.kind == "doc":
            space_vcs.apply_row(conn, ctx.account, ctx.space, ctx.plugin, "doc", it.key, value=prep.sealed,
                                size=prep.size, sha256=prep.sha256, rev=seq, dirty=False, state=state, error=error)
            space_vcs.base_put(conn, ctx.account, ctx.space, ctx.plugin, it.key, seq, prep.sha256, prep.sealed)
        else:
            space_vcs.apply_row(conn, ctx.account, ctx.space, ctx.plugin, "asset", it.key, value=None,
                                size=prep.size, sha256=prep.sha256, rev=seq, dirty=False, state=state, error=error)
            # Last, inside the transaction: the row and the file land together, and a file
            # held open fails here, before anything of this item changed.
            self.files.aside(prep.target)
            self.files.move(prep.temp, prep.target, keep=True)
        self.changed.append(it.key)

    def _delete(self, it: IncomingItem, seq: int, by: str, at: float, cur: dict[str, Any] | None) -> None:
        ctx, conn = self.ctx, self.conn
        a = (ctx.account, ctx.space, ctx.plugin, it.kind, it.key)
        space_vcs.blocked_delete(conn, *a)
        if cur is None:
            space_vcs.base_delete(conn, ctx.account, ctx.space, ctx.plugin, it.key)
            return
        if int(cur["dirty"]) and not int(cur["deleted"]):
            # Deleted there, changed here: mine stays (out of pushes) until the person picks.
            space_vcs.conflict_put(conn, *a, unit=unit_of(it.kind, it.key), reason="deleted",
                                   mine_value=cur["value"], mine_sha=cur["sha256"], mine_size=int(cur["size"]),
                                   base_seq=int(cur["rev"]), theirs_seq=seq, theirs_by=by, theirs_at=at,
                                   theirs_deleted=True)
            space_vcs.set_row_state(conn, *a, "conflict", f"{_who(by)} deleted this; you changed it.", rev=seq)
            space_vcs.base_delete(conn, ctx.account, ctx.space, ctx.plugin, it.key)
            self.conflicts += 1
            return
        old = space_vcs.conflict_get(*a, conn=conn)
        if not int(cur["dirty"]) and _holds_mine(old) and self._mine_back(it, seq, by, at, old):
            return
        space_vcs.delete_row(conn, *a)
        space_vcs.conflict_delete(conn, *a)
        space_vcs.base_delete(conn, ctx.account, ctx.space, ctx.plugin, it.key)
        if it.kind == "asset":
            self.files.aside(_asset_path(ctx, it.key))
        self.changed.append(it.key)

    def _mine_back(self, it: IncomingItem, seq: int, by: str, at: float, c: Mapping[str, Any]) -> bool:
        """Deleted there while an unresolved conflict still keeps my version (the row holds
        theirs): mine becomes the row again, an edit against the delete, so the delete never
        takes it with it. False when mine's side copy is gone (nothing left to keep)."""
        ctx, conn = self.ctx, self.conn
        a = (ctx.account, ctx.space, ctx.plugin, it.kind, it.key)
        error = f"{_who(by)} deleted this; you changed it."
        mine_value = c["mine_value"] if it.kind == "doc" else None
        if it.kind == "asset" and not Path(c["mine_path"]).is_file():
            return False
        space_vcs.apply_row(conn, *a, value=mine_value, size=int(c["mine_size"]), sha256=str(c["mine_sha"]), rev=seq,
                            dirty=True, state="conflict", error=error)
        space_vcs.conflict_put(conn, *a, unit=unit_of(it.kind, it.key), reason="deleted", mine_value=mine_value,
                               mine_sha=str(c["mine_sha"]), mine_size=int(c["mine_size"]), base_seq=int(c["base_seq"]),
                               theirs_seq=seq, theirs_by=by, theirs_at=at, theirs_deleted=True)
        space_vcs.base_delete(conn, ctx.account, ctx.space, ctx.plugin, it.key)
        if it.kind == "asset":
            target = _asset_path(ctx, it.key)
            self.files.aside(target)
            self.files.move(Path(c["mine_path"]), target, keep=True)
        self.changed.append(it.key)
        return True

    def _merge_doc(self, it: IncomingItem, seq: int, by: str, at: float, cur: dict[str, Any], prep: _Prep) -> None:
        ctx, conn = self.ctx, self.conn
        a = (ctx.account, ctx.space, ctx.plugin, "doc", it.key)
        rev = int(cur["rev"])
        mine = json.loads(data_crypto.open_text(cur["value"], ctx.account))
        theirs = json.loads(prep.raw)
        # The base is the version mine is an edit of: kept here, or fetched for this apply.
        # rev 0: both sides made the doc (the base is "no doc"). A base that is no longer
        # known never merges: keys only one side holds would be taken as additions, and a
        # teammate's delete would come back.
        base: Any = json_merge.MISSING
        known = self.merge and rev == 0
        base_row = space_vcs.base_get(ctx.account, ctx.space, ctx.plugin, it.key, conn=conn)
        if self.merge and base_row and int(base_row["seq"]) == rev and base_row["value"] is not None:
            base, known = json.loads(data_crypto.open_text(base_row["value"], ctx.account)), True
        elif self.merge and (it.key, rev) in self.bases:
            base, known = json.loads(self.bases[(it.key, rev)]), True
        if known:
            result = json_merge.merge(base, theirs, mine, set_arrays=ctx.plugin == WORKFLOWS and it.key == FOLDERS_KEY)
        else:
            result = json_merge.MergeResult(theirs, [] if json_merge.same(theirs, mine) else ["$"])
        if result.clean:
            raw = scopes.encode_doc(result.value)
            sha = _sha(raw)
            if sha == prep.sha256:
                self._theirs(it, seq, prep, keep_conflict=_has_conflict(conn, ctx, "doc", it.key))
                return
            sealed = data_crypto.seal_text(raw.decode("utf-8"), ctx.account)
            space_vcs.apply_row(conn, *a, value=sealed, size=len(raw), sha256=sha, rev=seq, dirty=True,
                                group_id=self._merge_group(by))
            space_vcs.base_put(conn, ctx.account, ctx.space, ctx.plugin, it.key, seq, prep.sha256, prep.sealed)
            self.changed.append(it.key)
            self.merges += 1
            return
        if ctx.plugin == WORKFLOWS:
            from backend.automations import owned

            owned.archive_before_adopt(ctx.scope, it.key)  # "Your copy before sync" in its History
        space_vcs.conflict_put(conn, *a, unit=unit_of("doc", it.key), reason="changed", mine_value=cur["value"],
                               mine_sha=cur["sha256"], mine_size=int(cur["size"]), base_seq=rev,
                               theirs_seq=seq, theirs_by=by, theirs_at=at)
        self._theirs(it, seq, prep, keep_conflict=True, error=f"{_who(by)} changed this too.")
        self.conflicts += 1

    def _keep_both_files(self, it: IncomingItem, seq: int, by: str, at: float, cur: dict[str, Any],
                         prep: _Prep) -> None:
        """Files are never merged: theirs is applied, mine is kept as a sealed side copy."""
        ctx, conn = self.ctx, self.conn
        a = (ctx.account, ctx.space, ctx.plugin, "asset", it.key)
        old = space_vcs.conflict_get(*a, conn=conn)
        mine_path = ""
        side = _conflicts_dir(ctx) / f"{uuid.uuid4().hex}.bin"
        src = _asset_path(ctx, it.key)
        if src.is_file():
            self.files.move(src, side, keep=True)  # mine, sealed as it is; put back if this rolls back
            mine_path = str(side)
        if old is not None and old["mine_path"] and old["mine_path"] != mine_path:
            self.cleanup.append(Path(old["mine_path"]))
        space_vcs.conflict_put(conn, *a, unit=unit_of("asset", it.key), reason="changed", mine_path=mine_path,
                               mine_sha=cur["sha256"], mine_size=int(cur["size"]), base_seq=int(cur["rev"]),
                               theirs_seq=seq, theirs_by=by, theirs_at=at)
        self._theirs(it, seq, prep, keep_conflict=True, error=f"{_who(by)} changed this file too.")
        self.conflicts += 1

    def undo(self) -> None:
        self.files.undo()

    def committed(self) -> None:
        self.files.committed()
        for path in self.cleanup:
            path.unlink(missing_ok=True)


def _fetch_bases(ctx: Ctx, wanted: Iterable[tuple[str, int]]) -> dict[tuple[str, int], bytes]:
    """Merge bases this PC doesn't keep (a doc edited here whose base was never stored, or
    went with a replay): each version mine is an edit of, fetched. One that can't be fetched
    is left out (the doc then doesn't merge)."""
    out: dict[tuple[str, int], bytes] = {}
    for key, seq in dict.fromkeys(wanted):
        got = _call(ctx.backend.fetch, ctx.space, ctx.plugin, "doc", key, seq)
        try:
            if isinstance(got, IncomingItem) and got.op == "put":
                raw = got.read()
                if (not got.sha256 or got.sha256 == _sha(raw)) and isinstance(json.loads(raw), (dict, list)):
                    out[(key, seq)] = raw
        except Exception:  # noqa: BLE001 — no base: no merge
            _log.debug("base %s@%s of %s couldn't be read", key, seq, ctx.plugin, exc_info=True)
        finally:
            _drop_temp(got)
    return out


def _by(ctx: Ctx, it: IncomingItem, cs: IncomingChangeset) -> str:
    """:func:`author` of an incoming item: its own author, else its changeset's."""
    if it.by and it.by != cs.by:
        return it.by
    return author(ctx.account, ctx.space, cs.by, cs.device, cs.actor, cs.ducky)


def _apply(ctx: Ctx, changesets: list[IncomingChangeset], *, force: bool = False) -> dict[str, Any]:
    """Download, then apply ``changesets`` in one transaction (the head moves to the last
    one's seq). ``force``: a clean row takes theirs even at the same or a newer rev (a
    resync). Folder files land first, unit by unit (:func:`_apply_tree`), so the head never
    moves past a unit that didn't land or wasn't held. Units are claimed at their seq unless
    an item of them is still set aside (:func:`space_vcs.unit_claim`); a folder unit whole
    again is claimed at the head this leaves (``upto`` of :func:`_apply_tree`). Returns
    ``{state, changed, conflicts, merges, blocked, blocked_units, tree}`` (``tree``: folder
    units that changed)."""
    latest: dict[ItemId, tuple[IncomingItem, int, str, float]] = {}
    units: dict[str, int] = {}
    tree: list[tuple[IncomingItem, int, str, float]] = []
    for cs in changesets:
        for it in cs.items:
            if it.kind == TREE:
                tree.append((it, int(it.seq or cs.seq), _by(ctx, it, cs), float(it.at or cs.at)))
                continue
            if it.kind not in KINDS:
                continue
            seq = int(it.seq or cs.seq)
            latest[it.id] = (it, seq, _by(ctx, it, cs), float(it.at or cs.at))
            unit = it.unit or unit_of(it.kind, it.key)
            units[unit] = max(units.get(unit, 0), seq)
    work = []
    wanted: list[tuple[str, int]] = []
    for it, seq, by, at in latest.values():
        cur = space_vcs.row(None, ctx.account, ctx.space, ctx.plugin, it.kind, it.key)
        if cur is not None and int(cur["rev"]) >= seq and (int(cur["dirty"]) or not force):
            continue
        work.append((it, seq, by, at))
        if (it.kind == "doc" and it.op == "put" and cur is not None and int(cur["dirty"]) and not int(cur["deleted"])
                and int(cur["rev"]) > 0):
            b = space_vcs.base_get(ctx.account, ctx.space, ctx.plugin, it.key)
            if not b or int(b["seq"]) != int(cur["rev"]) or b["value"] is None:
                wanted.append((it.key, int(cur["rev"])))
    last = max((cs.seq for cs in changesets), default=0)
    upto = last or int(space_vcs.head_get(ctx.account, ctx.space, ctx.plugin)["seq"])
    folded = (_apply_tree(ctx, tree, upto=upto) if tree
              else {"state": "ok", "units": [], "conflicts": 0, "blocked": 0})
    if folded["state"] != "ok":
        return {"state": folded["state"], "error": folded.get("error", "")}
    fetched = _download(ctx, [it for it, *_ in work if it.op == "put"])
    if any(isinstance(got, Offline) for got in fetched.values()):
        for got in fetched.values():
            _drop_temp(got)
        return {"state": "offline", "error": reason_of(next(g for g in fetched.values() if isinstance(g, Offline)))}
    preps: dict[ItemId, _Prep] = {}
    try:
        for it, *_ in work:
            if it.op == "put":
                preps[it.id] = _prepare(ctx, it, fetched.get(it.id))
        bases = _fetch_bases(ctx, wanted) if wanted else {}
        conn = db.connect()
        run = _Apply(ctx, conn, force=force, bases=bases)
        try:
            with db.write_txn(conn):
                for it, seq, by, at in work:
                    run.one(it, seq, by, at, preps.get(it.id))
                for unit, seq in units.items():
                    if unit not in run.blocked:
                        space_vcs.unit_claim(conn, ctx.account, ctx.space, ctx.plugin, unit, seq)
                if last:
                    space_vcs.head_advance(conn, ctx.account, ctx.space, ctx.plugin, last)
                if run.changed:
                    space_vcs.change_log_bump(conn, ctx.plugin, run.changed)
        except BaseException:
            run.undo()  # the transaction rolled back: so do the files
            raise
        run.committed()
    finally:
        for prep in preps.values():
            if prep.temp is not None:
                prep.temp.unlink(missing_ok=True)  # gone already once it landed
    return {"state": "ok", "changed": run.changed, "conflicts": run.conflicts + folded["conflicts"],
            "merges": run.merges, "blocked": len(run.blocked) + folded["blocked"], "blocked_units": set(run.blocked),
            "tree": len(folded["units"])}


def _settle_in_flight(ctx: Ctx, *, now: float) -> _Out | None:
    """Changesets already sent once are answered before anything is pulled: one the server
    committed (its answer lost) would otherwise come back in the pull looking like someone
    else's change to the same rows. So an error to one of them (a rate limit can answer
    before the server looks at the changeset) never drops it: the round backs off and it
    goes again. Returns why the round must stop, or None."""
    for cs in space_vcs.cs_open(ctx.account, ctx.space, ctx.plugin):
        if cs["state"] != "pushing":
            continue
        out = _push_cs(ctx, cs, now=now)
        if out.kind == "missing":
            again = space_vcs.cs_get(ctx.account, ctx.space, cs["cs_id"])
            out = _push_cs(ctx, again, now=now) if again else _Out("dropped")
            if out.kind == "missing":
                return _stop(ctx, Error(out.error), now=now)
        if out.kind == "stop":
            return out
    return None


def _deliver(ctx: Ctx, keys: list[str]) -> None:
    """The plugin's ``api.data.on_change`` callbacks in this process hear what pulls changed:
    this one's keys, and those of any pull in another process (the MCP bridge) since they
    last heard (the plugin's change log has both)."""
    if keys:
        scopes.deliver_data_changes(ctx.plugin)


def _pull_round(ctx: Ctx, *, now: float, apply_now: bool = False,
                progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Pull to the head. Whatever it changed, even when it stops part way, is told to the
    plugin once at the end. A ``reload`` plugin whose panel is on screen gets its changes
    only when asked (``apply_now``: Update now, Pull, an agent's call, a switch); until then
    they wait behind "Update now" (``queued``). ``progress(seq, head)`` after each landing."""
    changed: list[str] = []
    try:
        return _pull_pages(ctx, changed, now=now, apply_now=apply_now, progress=progress)
    finally:
        _deliver(ctx, changed)


def _pull_pages(ctx: Ctx, changed: list[str], *, now: float, apply_now: bool = False,
                progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    settled = _settle_in_flight(ctx, now=now)
    mine: dict[str, dict[str, Any]] = {}
    if settled is not None:
        # A resend got no answer. Its commit, if it landed, comes back in the pull under its
        # cs_id and is settled there, so the team's changes still come in meanwhile (else
        # one resend the server keeps failing holds every incoming change).
        mine = {cs["cs_id"]: cs for cs in space_vcs.cs_open(ctx.account, ctx.space, ctx.plugin)
                if cs["state"] == "pushing"}
        if not mine or any(_is_tree_cs(cs) for cs in mine.values()):
            return {"state": settled.state, "error": settled.error, "changed": [], "pulled": 0, "conflicts": 0}
    out = _pull_from(ctx, changed, mine, now=now, apply_now=apply_now, progress=progress)
    if settled is not None and mine and out["state"] == "ok":
        out = {**out, "state": settled.state, "error": settled.error}  # still unanswered: the round backs off
    return out


def _own_commit(ctx: Ctx, cs: dict[str, Any], seq: int) -> None:
    """A changeset sent from here whose answer never came, found committed in the pull:
    settled as a replay is (:func:`_push_cs`), so it never lands as someone else's change."""
    snaps = cs["items"]
    space_vcs.mark_committed(ctx.account, ctx.space, ctx.plugin, seq, snaps,
                             units=sorted({str(s.get("unit") or unit_of(str(s["kind"]), str(s["key"])))
                                           for s in snaps}))
    space_vcs.cs_set(ctx.account, ctx.space, cs["cs_id"], state="committed", seq=seq)


def _pull_from(ctx: Ctx, changed: list[str], mine: dict[str, dict[str, Any]], *, now: float,
               apply_now: bool = False, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Pull to the head. ``mine``: changesets sent from here still unanswered (cs_id → row);
    each one found in the pull is settled as this PC's own commit and taken out."""
    head = space_vcs.head_get(ctx.account, ctx.space, ctx.plugin)
    cursor = int(head["seq"])
    hold = not apply_now and _holds_incoming(ctx)
    me = device_id() if hold else ""
    buffer: list[IncomingChangeset] = []
    waiting: list[IncomingChangeset] = []
    conflicts = pulled = tree = 0
    for _ in range(MAX_PAGES):
        page = _call(ctx.backend.pull, ctx.space, ctx.plugin, cursor, PULL_LIMIT)
        if not isinstance(page, PullPage):
            out = _stop(ctx, page, now=now)
            return {"state": out.state, "error": out.error, "changed": changed, "pulled": pulled,
                    "conflicts": conflicts, "tree": tree}
        if page.resync:
            if mine:  # a resync lists items, not changesets: an unanswered one of mine can't be told apart
                out = _stop(ctx, Error("A change sent from here is still unanswered."), now=now)
                return {"state": out.state, "error": out.error, "changed": changed, "pulled": pulled,
                        "conflicts": conflicts, "tree": tree}
            return _resync(ctx, page.head, changed, now=now)
        space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, remote_seq=page.head)
        for cs in page.changesets:
            cursor = max(cursor, cs.seq)
            if cs.cs_id and cs.cs_id in mine:
                _own_commit(ctx, mine.pop(cs.cs_id), cs.seq)
            if hold and (waiting or cs.device != me):
                waiting.append(cs)  # behind Update now (this PC's own commits still land)
                continue
            buffer.append(cs)
            if _groups_open(buffer, now):
                continue
            out = _apply(ctx, buffer)
            if out["state"] != "ok":
                stop = _stop(ctx, Offline(out.get("error", "")), now=now)
                return {"state": stop.state, "error": stop.error, "changed": changed, "pulled": pulled,
                        "conflicts": conflicts, "tree": tree}
            changed += out["changed"]
            conflicts += out["conflicts"]
            tree += int(out.get("tree") or 0)
            pulled += len(buffer)
            buffer = []
            if progress is not None:
                progress(cs.seq, page.head)
        if not page.more:
            break
    if hold or space_vcs.incoming_queue_get(ctx.account, ctx.space, ctx.plugin)["count"]:
        _wait_behind_update(ctx, waiting)
    if waiting:
        return {"state": "ok", "error": "", "changed": changed, "pulled": pulled, "conflicts": conflicts,
                "tree": tree, "queued": len(waiting)}
    blocked = _retry_blocked(ctx, now=now)
    changed += blocked["changed"]
    again = _refetch(ctx) if tf.plugin_folders(ctx.plugin) else {"units": [], "conflicts": 0}
    return {"state": blocked["state"], "error": blocked.get("error", ""), "changed": changed, "pulled": pulled,
            "conflicts": conflicts + blocked["conflicts"] + again["conflicts"],
            "tree": tree + blocked.get("tree", 0) + len(again["units"])}


def _retry_blocked(ctx: Ctx, *, now: float) -> dict[str, Any]:
    """Items set aside whose wait is over, each alone at its current version (a folder unit's
    files together: a unit is never applied in halves)."""
    changed: list[str] = []
    conflicts = tree = 0
    units: dict[str, list[dict[str, Any]]] = {}
    for b in space_vcs.blocked(ctx.account, ctx.space, ctx.plugin, due=now):
        if b["kind"] == TREE:
            units.setdefault(str(b["unit"]), []).append(b)
            continue
        got = _call(ctx.backend.fetch, ctx.space, ctx.plugin, b["kind"], b["key"], None)
        if isinstance(got, Offline):
            return {"state": "ok", "changed": changed, "conflicts": conflicts, "tree": tree}  # the next round tries again
        if not isinstance(got, IncomingItem):
            conn = db.connect()
            with db.write_txn(conn):
                space_vcs.blocked_put(conn, ctx.account, ctx.space, ctx.plugin, b["kind"], b["key"], unit=b["unit"],
                                      seq=int(b["seq"]), error=reason_of(got), now=now)
            continue
        out = _apply(ctx, [IncomingChangeset(seq=0, items=(got,))], force=True)
        changed += out.get("changed", [])
        conflicts += int(out.get("conflicts", 0))
    for rows in units.values():
        gots: list[IncomingItem] = []
        for b in rows:
            got = _call(ctx.backend.fetch, ctx.space, ctx.plugin, TREE, b["key"], None)
            if isinstance(got, Offline):
                for g in gots:
                    _drop_temp(g)
                return {"state": "ok", "changed": changed, "conflicts": conflicts, "tree": tree}
            if isinstance(got, IncomingItem):
                gots.append(got)
                continue
            conn = db.connect()
            with db.write_txn(conn):
                space_vcs.blocked_put(conn, ctx.account, ctx.space, ctx.plugin, TREE, b["key"], unit=b["unit"],
                                      seq=int(b["seq"]), error=reason_of(got), now=now)
        if gots:
            out = _apply(ctx, [IncomingChangeset(seq=0, items=tuple(gots))])
            conflicts += int(out.get("conflicts", 0))
            tree += int(out.get("tree") or 0)
    return {"state": "ok", "changed": changed, "conflicts": conflicts, "tree": tree}


def _listing(ctx: Ctx, *, now: float,
             kinds: tuple[str, ...] = KINDS) -> tuple[dict[ItemId, IncomingItem], int] | dict[str, Any]:
    """Every item's current version on the server (``pull(full=True)``, paged) and the head
    the listing surely covers: the first page's. A commit landing while it pages moves later
    pages' head, but pages already read never list it, so the head stays before it and a
    pull brings it (rows already at its seq are skipped). Or why the round stops."""
    listing: dict[ItemId, IncomingItem] = {}
    after = ""
    head = -1
    while True:
        page = _call(ctx.backend.pull, ctx.space, ctx.plugin, 0, PULL_LIMIT, full=True, after=after)
        if not isinstance(page, PullPage):
            out = _stop(ctx, page, now=now)
            return {"state": out.state, "error": out.error, "changed": [], "pulled": 0, "conflicts": 0}
        if head < 0:
            head = int(page.head)
        for it in page.items:
            if it.kind in kinds:
                listing[it.id] = it
        if not page.more or not page.after or page.after == after:
            return listing, head
        after = page.after


def _resync(ctx: Ctx, head_seq: int, changed: list[str], *, now: float) -> dict[str, Any]:
    """The cursor is older than what the server keeps: list every item and reconcile each
    by seq (missing here: applied; extra here and clean: removed; edited here: the usual
    merge and conflict rules; folder units as a pull applies them)."""
    got = _listing(ctx, now=now, kinds=(*KINDS, TREE))
    if isinstance(got, dict):
        return got
    listing, _ = got
    tree = {vid: it for vid, it in listing.items() if vid[0] == TREE}
    listing = {vid: it for vid, it in listing.items() if vid[0] != TREE}
    folded = _resync_tree(ctx, tree, head_seq)
    if folded["state"] != "ok":
        stop = _stop(ctx, Offline(folded.get("error", "")), now=now)
        return {"state": stop.state, "error": stop.error, "changed": changed, "pulled": 0, "conflicts": 0}
    local = {(r["kind"], r["key"]): r for r in space_vcs.plugin_rows(ctx.account, ctx.space, ctx.plugin)
             if r["kind"] in KINDS}
    todo: list[IncomingItem] = []
    for vid, it in listing.items():
        cur = local.get(vid)
        if it.deleted:
            if cur is not None:
                todo.append(it)
            continue
        if (cur is not None and not int(cur["dirty"]) and not int(cur["deleted"]) and int(cur["rev"]) == it.seq
                and (not it.sha256 or it.sha256 == cur["sha256"])):
            continue
        todo.append(it)
    conflicts = 0
    blocked: set[str] = set()
    for i in range(0, len(todo), PULL_LIMIT):
        out = _apply(ctx, [IncomingChangeset(seq=0, items=tuple(todo[i:i + PULL_LIMIT]))], force=True)
        if out["state"] != "ok":
            stop = _stop(ctx, Offline(out.get("error", "")), now=now)
            return {"state": stop.state, "error": stop.error, "changed": changed, "pulled": 0, "conflicts": conflicts}
        changed += out["changed"]
        conflicts += out["conflicts"]
        blocked |= out["blocked_units"]
    extras = [r for vid, r in local.items() if vid not in listing and not int(r["dirty"]) and not int(r["sensitive"])]
    conn = db.connect()
    run = _Apply(ctx, conn, force=True)
    try:
        with db.write_txn(conn):
            for r in extras:
                cur = space_vcs.row(conn, ctx.account, ctx.space, ctx.plugin, r["kind"], r["key"])
                if (cur is None or int(cur["dirty"]) or cur["sha256"] != r["sha256"]
                        or float(cur["updated"]) != float(r["updated"])):
                    continue  # written since the listing: it stays, and its push settles it
                gone = IncomingItem(kind=r["kind"], key=r["key"], op="del")
                run.one(gone, 0, "", now, None, action=lambda gone=gone, cur=cur: run._delete(gone, 0, "", now, cur))
            blocked |= {b["unit"] for b in space_vcs.blocked(ctx.account, ctx.space, ctx.plugin)}
            for it in listing.values():  # every unit applied here is as the server has it (what lock requests say)
                unit = it.unit or unit_of(it.kind, it.key)
                if unit not in blocked:
                    space_vcs.unit_seen(conn, ctx.account, ctx.space, ctx.plugin, unit, it.seq)
            space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, conn=conn, remote_seq=head_seq)
            space_vcs.head_reset(ctx.account, ctx.space, ctx.plugin, head_seq, conn=conn)
            if run.changed:
                space_vcs.change_log_bump(conn, ctx.plugin, run.changed)
    except BaseException:
        run.undo()
        raise
    run.committed()
    changed += run.changed
    return {"state": "ok", "error": "", "changed": changed, "pulled": len(todo) + len(tree),
            "conflicts": conflicts + folded["conflicts"], "resync": True, "tree": len(folded["units"])}


def _resync_tree(ctx: Ctx, listed: Mapping[ItemId, IncomingItem], head_seq: int) -> dict[str, Any]:
    """A resync's folder files: each one's current version lands as a pull would land it
    (one here at that seq stays), and a file here the team no longer has, sent before and
    not changed since, goes."""
    if not listed and not tf.plugin_folders(ctx.plugin):
        return {"state": "ok", "units": [], "conflicts": 0}
    entries = [(it, int(it.seq), it.by, float(it.at)) for it in listed.values()]
    keys = {vid[1] for vid in listed}
    for folder in tf.plugin_folders(ctx.plugin):
        for r in ff.files(ctx.account, ctx.space, ctx.plugin, folder.id):
            if r["item_key"] and r["item_key"] not in keys and int(r["base_seq"]) and not r["dirty"]:
                entries.append((IncomingItem(kind=TREE, key=r["item_key"], op="del", seq=int(head_seq)),
                                int(head_seq), "", 0.0))
    return _apply_tree(ctx, entries) if entries else {"state": "ok", "units": [], "conflicts": 0}


def _v1rev(it: IncomingItem) -> int | None:
    try:
        v = it.meta.get("v1rev")
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _keeps(cur: Mapping[str, Any] | None, it: IncomingItem) -> bool:
    """On the move from the older sync, this PC's row stays as it is (at the item's seq):
    clean with the item's content, or an edit made on top of the revision it was imported at."""
    if cur is None or it.deleted:
        return False
    if not int(cur["dirty"]):
        return not int(cur["deleted"]) and bool(it.sha256) and it.sha256 == cur["sha256"]
    v1rev = _v1rev(it)
    return v1rev is not None and int(cur["rev"]) == v1rev


def _reconcile_v1(ctx: Ctx, *, now: float) -> dict[str, Any]:
    """The plugin's first round on the new sync. Its rows still carry the older sync's
    revisions, which mean nothing to the new one, so they are matched once against a full
    listing (an imported item says the older revision it came from, ``meta["v1rev"]``):

    - clean here with the item's content, or edited here on top of the revision it was
      imported at: kept as it is, now at the item's seq (an edit goes out as usual);
    - clean here and different, or not here: the team's version comes down;
    - edited here on top of anything else: a conflict (theirs current, mine kept);
    - here and not on the server: removed when clean, an edit against a delete when it
      was edited on top of a revision, sent as new when it was made here.

    All in one transaction; only then is the plugin on the new sync (engine ``v2``) with
    the server's head. Folder files the listing has land first, as a resync lands them
    (the head jumps past their changesets). A copy with nothing from the older sync (no
    rows, or only changes made here and never sent) needs none of it: it pulls from the
    start like any."""
    if not any(int(r["rev"]) > 0 or not int(r["dirty"])
               for r in space_vcs.plugin_rows(ctx.account, ctx.space, ctx.plugin)
               if r["kind"] in KINDS and not int(r["sensitive"])):
        space_vcs.head_put(ctx.account, ctx.space, ctx.plugin, engine="v2")
        return {"state": "ok", "error": "", "changed": [], "pulled": 0, "conflicts": 0}
    got = _listing(ctx, now=now, kinds=(*KINDS, TREE))
    if isinstance(got, dict):
        return got
    listing, head_seq = got
    tree = {vid: it for vid, it in listing.items() if vid[0] == TREE}
    listing = {vid: it for vid, it in listing.items() if vid[0] != TREE}
    if tree:
        folded = _resync_tree(ctx, tree, head_seq)
        if folded["state"] != "ok":
            out = _stop(ctx, Offline(folded.get("error", "")), now=now)
            return {"state": out.state, "error": out.error, "changed": [], "pulled": 0, "conflicts": 0}
    local = {(r["kind"], r["key"]): r for r in space_vcs.plugin_rows(ctx.account, ctx.space, ctx.plugin)
             if r["kind"] in KINDS}
    need = [it for vid, it in listing.items() if not it.deleted and not _keeps(local.get(vid), it)]
    fetched = _download(ctx, need)
    if any(isinstance(g, Offline) for g in fetched.values()):
        for g in fetched.values():
            _drop_temp(g)
        out = _stop(ctx, next(g for g in fetched.values() if isinstance(g, Offline)), now=now)
        return {"state": out.state, "error": out.error, "changed": [], "pulled": 0, "conflicts": 0}
    preps: dict[ItemId, _Prep] = {}
    a = (ctx.account, ctx.space, ctx.plugin)
    try:
        for it in need:
            preps[it.id] = _prepare(ctx, it, fetched.get(it.id))
        conn = db.connect()
        run = _Apply(ctx, conn, force=True, merge=False)
        try:
            with db.write_txn(conn):
                rows = {(r["kind"], r["key"]): r for r in space_vcs.plugin_rows(*a) if r["kind"] in KINDS}
                for vid, it in listing.items():
                    cur = rows.get(vid)
                    if cur is not None and int(cur["sensitive"]):
                        continue
                    if _keeps(cur, it):
                        conn.execute("UPDATE plugin_data SET rev=? WHERE account_id=? AND scope_id=? AND plugin_id=? "
                                     "AND kind=? AND key=?", (int(it.seq), *a, *vid))
                        if it.kind == "doc" and not int(cur["dirty"]):
                            space_vcs.base_put(conn, *a, it.key, int(it.seq), cur["sha256"], cur["value"])
                        continue
                    if cur is not None:  # a revision of the older sync is no base here
                        conn.execute("UPDATE plugin_data SET rev=0 WHERE account_id=? AND scope_id=? AND plugin_id=? "
                                     "AND kind=? AND key=?", (*a, *vid))
                    prep = preps.get(it.id)
                    if not it.deleted and prep is None:  # changed here during the listing: fetched alone
                        space_vcs.blocked_put(conn, *a, it.kind, it.key, unit=it.unit or unit_of(*vid), seq=it.seq,
                                              error="Changed here while moving to the new sync.", now=0.0)
                        run.blocked.add(it.unit or unit_of(*vid))
                        continue
                    run.one(it, int(it.seq), it.by, it.at, prep)
                for vid, cur in rows.items():
                    if vid in listing or int(cur["sensitive"]) or (int(cur["dirty"]) and not int(cur["deleted"])
                                                                   and not int(cur["rev"])):
                        continue  # on the server, never shared, or made here: it goes out as new
                    gone = IncomingItem(kind=vid[0], key=vid[1], op="del")
                    run.one(gone, 0, "", now, None, action=lambda gone=gone, cur=cur: run._delete(gone, 0, "", now, cur))
                for it in listing.values():
                    unit = it.unit or unit_of(it.kind, it.key)
                    if unit not in run.blocked:
                        space_vcs.unit_seen(conn, *a, unit, int(it.seq))
                space_vcs.head_reset(*a, head_seq, conn=conn)
                space_vcs.head_put(*a, conn=conn, engine="v2", remote_seq=head_seq)
                if run.changed:
                    space_vcs.change_log_bump(conn, ctx.plugin, run.changed)
        except BaseException:
            run.undo()
            raise
        run.committed()
    finally:
        for prep in preps.values():
            if prep.temp is not None:
                prep.temp.unlink(missing_ok=True)
    return {"state": "ok", "error": "", "changed": run.changed, "pulled": len(need), "conflicts": run.conflicts}


# --------------------------------------------------------------------------- folders (kind tree)


def _is_tree_cs(cs: Mapping[str, Any]) -> bool:
    return any(i.get("kind") == TREE for i in cs.get("items") or [])


def _tree_folder_id(key: str) -> str:
    return key[len("tree:"):].split("/", 1)[0] if key.startswith("tree:") else ""


def _unit_key(folder_id: str, unit: str) -> str:
    """A folder unit's conflict key (``<folder>/<unit>``; the unit keeps its project id)."""
    return f"{folder_id}/{unit}"


def _unit_display(folder: tf.Folder, unit: str) -> str:
    """How a unit reads on the bar: its folder or file name (the folder's id for a whole folder)."""
    if folder.project:
        rest = unit.split("/", 1)[1] if "/" in unit else ""
        return rest or folder.id
    return folder.id if unit == "." else unit


def _uids(ctx: Ctx, folder: tf.Folder) -> Callable[[str], str]:
    return tf.unit_ids(ctx.backend, ctx.space, folder.id)


def _folder_copies(ctx: Ctx) -> list[tuple[tf.Folder, str]]:
    """Each copy of the plugin's declared folders in the space: ``(folder, project_id)``. A
    ``{PROJECT}`` folder has one per project this PC keeps files of."""
    out: list[tuple[tf.Folder, str]] = []
    for folder in tf.plugin_folders(ctx.plugin):
        if not folder.project:
            out.append((folder, ""))
            continue
        pids = {str(r[0]) for r in db.connect().execute(
            "SELECT DISTINCT project_id FROM folder_files WHERE account_id=? AND space_id=? AND plugin_id=? AND "
            "folder_id=? AND project_id<>''", (ctx.account, ctx.space, ctx.plugin, folder.id)).fetchall()}
        base = tf.team_copy(ctx.account, ctx.space, ctx.plugin, folder)
        pids |= {e.name for e in tf._entries(base) if e.is_dir(follow_symlinks=False) and not e.is_junction()}
        out += [(folder, pid) for pid in sorted(pids)]
    return out


def _copy_units(ctx: Ctx, folder: tf.Folder, project_id: str) -> list[str]:
    return [str(r[0]) for r in db.connect().execute(
        "SELECT DISTINCT unit FROM folder_files WHERE account_id=? AND space_id=? AND plugin_id=? AND folder_id=? AND "
        "project_id=?", (ctx.account, ctx.space, ctx.plugin, folder.id, project_id)).fetchall()]


def _holds_incoming(ctx: Ctx) -> bool:
    """Incoming changes wait behind "Update now": a ``reload`` plugin whose panel is on
    screen, unless it is live (its panels stay loaded and merge them, ``live.panels_kept``)."""
    if not any(f.after_pull == "reload" for f in tf.plugin_folders(ctx.plugin)):
        return False
    try:
        from backend.uefn_plugins import live

        if live.is_live(ctx.plugin):
            return False
    except Exception:  # noqa: BLE001 — not live: as before
        _log.debug("live check failed", exc_info=True)
    try:
        from backend.uefn_plugins.panel_presence import panel_state

        return bool(panel_state(ctx.plugin)["visible"])
    except Exception:  # noqa: BLE001 — unsure: apply them
        return False


def _protect(ctx: Ctx) -> None:
    """Read-only exactly where it should be in every copy: units someone else holds (unless
    the folder quarantines), units with changes waiting behind Update now."""
    waiting = set(space_vcs.incoming_queue_get(ctx.account, ctx.space, ctx.plugin)["units"])
    for folder, pid in _folder_copies(ctx):
        uid = _uids(ctx, folder)
        incoming = [u for u in _copy_units(ctx, folder, pid) if uid(u) in waiting] if waiting else []
        try:
            tf.refresh_readonly(ctx.account, ctx.space, ctx.plugin, folder, project_id=pid, unit_id=uid,
                                incoming=incoming)
        except OSError:
            _log.debug("read-only refresh of %s failed", folder.id, exc_info=True)


def _auto_lock(ctx: Ctx, folder: tf.Folder, changed: Iterable[str], uid: Callable[[str], str], *, now: float) -> None:
    """A unit's first change here takes its lock (``lock: unit``). Refused because someone
    else holds it: the change was made before this PC knew, so it is quarantined."""
    if folder.lock != "unit":
        return
    a = (ctx.account, ctx.space, ctx.plugin)
    mine = {lk["unit"] for lk in space_vcs.locks(*a, now=now) if lk["mine"]}
    dirty = ff.dirty_units(*a, folder.id)
    want: list[tuple[str, str, str]] = []
    for unit in sorted(set(changed)):
        u = uid(unit)
        info = dirty.get((folder.id, unit))
        if u in mine or info is None or info["states"] & set(tf.HELD_STATES):
            continue
        if space_vcs.foreign_lock(*a, u, now=now):
            continue  # the scan quarantined it
        want.append((unit, u, _unit_display(folder, unit)))
    if not want:
        return
    got = acquire(*a, ctx.backend, [(u, name) for _, u, name in want], reason="auto", now=now)
    for unit, u, _ in want:
        ans = got.get(u) or {}
        if ans.get("state") == "locked":
            ff.unit_set_state(*a, folder.id, unit, "quarantined",
                              f"Changed here while {person(ans.get('holder'))} holds it")


def watch(account: str, space: str, plugin: str, backend: Any, *, now: float | None = None) -> dict[str, int]:
    """One look at a plugin's folder copies in a team space (the background sync's watcher,
    every 2 s while the plugin is in use and after each of its calls, else every minute):
    what changed is noted (presence says who edits what), a unit's first change takes its
    lock, and units someone else holds or with changes waiting behind Update now are
    read-only. Nothing goes out here. Returns ``{quiet, waiting, held}``: units ready to go
    out, still changing (or mid-save), held back (``busy`` when a round holds the space)."""
    now = time.time() if now is None else now
    out = {"quiet": 0, "waiting": 0, "held": 0}
    if not tf.plugin_folders(plugin) or held_reason(account, space):
        return out
    ctx = Ctx(account, space, plugin, backend)
    with _lock(account, space, 0.0) as got:
        if not got:
            return {**out, "busy": 1}
        for folder, pid in _folder_copies(ctx):
            uid = _uids(ctx, folder)
            sc = tf.scan(account, space, plugin, folder, project_id=pid, now=now, unit_id=uid)
            if sc.changed:
                _auto_lock(ctx, folder, sc.changed, uid, now=now)
            out["quiet"] += len(sc.quiet)
            out["waiting"] += len(sc.waiting)
            out["held"] += len(sc.held)
        _protect(ctx)
    return out


def _unit_actor(ctx: Ctx, unit_id: str, now: float) -> tuple[str, str]:
    """Who changed the unit last here (the person, or a ducky through the plugin's calls)."""
    for act in space_vcs.activity(ctx.account, ctx.space, now=now):
        if act["plugin_id"] == ctx.plugin and act["unit"] == unit_id:
            return str(act["actor"] or "user"), str(act["ducky"] or "")
    return "user", ""


def _unit_changeset(ctx: Ctx, folder: tf.Folder, pid: str, unit: str, unit_id: str, *, base_seq: int,
                    now: float) -> int:
    """One quiet unit as one changeset, written before any upload (a very big unit: parts of
    one group, the last one marked). Returns how many were written."""
    a = (ctx.account, ctx.space, ctx.plugin)
    items = tf.outgoing(*a, folder, unit, project_id=pid)
    if not items:
        return 0
    snaps: list[dict[str, Any]] = []
    for it in items:
        key = it["item_key"] or tf.tree_key(ctx.backend, ctx.space, folder.id, unit, it["relpath"])
        if not it["item_key"]:
            ff.file_put(None, *a, folder.id, it["relpath"], item_key=key)
        snaps.append({"kind": TREE, "key": key, "unit": unit_id, "op": it["op"], "sha256": it["sha256"],
                      "size": int(it["size"]), "deleted": 1 if it["op"] == "del" else 0, "relpath": it["relpath"],
                      "folder": folder.id, "project": pid, "unit_name": unit, "mtime_ns": int(it["mtime_ns"]),
                      "base_seq": int(it["base_seq"])})
    actor, ducky = _unit_actor(ctx, unit_id, now)
    parts = [snaps[i:i + MAX_ITEMS] for i in range(0, len(snaps), MAX_ITEMS)]
    gid = f"unit-{uuid.uuid4().hex}" if len(parts) > 1 else ""
    for n, part in enumerate(parts, 1):
        space_vcs.cs_add(*a, uuid.uuid4().hex, base_seq=base_seq, items=part, group_id=gid, part=n,
                         last=n == len(parts), message=_unit_display(folder, unit), actor=actor, ducky=ducky)
    return len(parts)


def _build_tree(ctx: Ctx, *, now: float, quiet_s: float = tf.QUIET_S) -> int:
    """Scan every folder copy and write a changeset per unit ready to go out: quiet for
    ``quiet_s`` seconds, no save in progress, not already going, not held (conflict,
    quarantine, refused, blocked), not waiting behind Update now, not waiting after a
    failed try. A unit someone else holds is quarantined. Returns how many changesets
    were written."""
    now = max(now, time.time())  # the round's start is older than a merge its pull just wrote
    a = (ctx.account, ctx.space, ctx.plugin)
    going = {(str(i.get("folder")), str(i.get("unit_name"))) for cs in space_vcs.cs_open(*a)
             for i in cs["items"] if i.get("kind") == TREE}
    waiting = set(space_vcs.incoming_queue_get(*a)["units"])
    head_seq = int(space_vcs.head_get(*a)["seq"])
    made = 0
    for folder, pid in _folder_copies(ctx):
        uid = _uids(ctx, folder)
        sc = tf.scan(*a, folder, project_id=pid, now=now, quiet_s=quiet_s, unit_id=uid)
        if sc.changed:
            _auto_lock(ctx, folder, sc.changed, uid, now=now)
        if not sc.quiet:
            continue
        dirty = ff.dirty_units(*a, folder.id)
        tries = space_vcs.unit_tries(*a)  # after the scan: a unit that changed again goes afresh
        for unit in sc.quiet:
            u = uid(unit)
            info = dirty.get((folder.id, unit))
            if ((folder.id, unit) in going or u in waiting or info is None or info["states"] & set(tf.HELD_STATES)
                    or float((tries.get(_unit_key(folder.id, unit)) or {}).get("next_try") or 0.0) > now):
                continue
            held = space_vcs.foreign_lock(*a, u, now=now)
            if held:
                ff.unit_set_state(*a, folder.id, unit, "quarantined", tf._held_text(held))
                continue
            made += _unit_changeset(ctx, folder, pid, unit, u, base_seq=head_seq, now=now)
    return made


def _tree_path(ctx: Ctx, snap: Mapping[str, Any]) -> str:
    folder = tf.folder_of(ctx.plugin, str(snap["folder"]))
    if folder is None:
        return ""
    pid = str(snap.get("project") or "")
    base = tf.team_copy(ctx.account, ctx.space, ctx.plugin, folder, pid)
    return os.path.join(tf._lp(base), *tf._bound_rel(str(snap["relpath"]), pid).split("/"))


def _tree_meta(snap: Mapping[str, Any]) -> dict[str, Any]:
    """What travels sealed with a folder file: its real name, unit and mtime."""
    return {"relpath": snap["relpath"], "unit_name": snap["unit_name"], "folder": snap["folder"],
            "project": snap.get("project") or "", "mtime_ns": int(snap.get("mtime_ns") or 0)}


def _units_of(snaps: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    """``{unit id: (folder id, unit)}`` of the snapshots."""
    return {str(s["unit"]): (str(s["folder"]), str(s["unit_name"])) for s in snaps}


def _tree_sent(ctx: Ctx, snaps: list[Mapping[str, Any]], seq: int) -> None:
    by_copy: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in snaps:
        by_copy.setdefault((str(s["folder"]), str(s.get("project") or "")), []).append(
            {"relpath": s["relpath"], "op": s["op"], "sha256": s["sha256"],
             "path": "" if s["op"] == "del" else _tree_path(ctx, s)})
    for (fid, pid), items in by_copy.items():
        folder = tf.folder_of(ctx.plugin, fid)
        if folder is not None:
            tf.mark_sent(ctx.account, ctx.space, ctx.plugin, folder, items, seq, project_id=pid)


def _push_tree_cs(ctx: Ctx, cs: dict[str, Any], *, now: float, fresh: bool | None = None) -> _Out:
    """One unit's changeset: each file is copied once into core's outbox, verified against
    what the changeset says (:func:`team_folders.snapshot`), and the backend reads the copy
    at its own pace, so the plugin's saves never wait for an upload and what goes out always
    matches its sha256. On a first try the unit must still be exactly what the changeset
    holds, else it goes again whole, later; a retry (its answer got lost) sends the same
    copies. ``fresh`` and errors: as :func:`_push_cs` (a big unit's later part that fails
    keeps its copies and goes again, so its group closes)."""
    a = (ctx.account, ctx.space, ctx.plugin)
    first_try = cs["state"] == "pending"
    fresh = first_try if fresh is None else fresh
    snaps = [s for s in cs["items"] if s.get("kind") == TREE]
    rows = {(str(s["folder"]), str(s["relpath"])): ff.file_get(*a, str(s["folder"]), str(s["relpath"]))
            for s in snaps}

    def dropped() -> _Out:
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        tf.drop_snapshots(*a, cs["cs_id"])
        return _Out("dropped")

    if first_try:
        for s in snaps:
            r = rows[(str(s["folder"]), str(s["relpath"]))]
            if (r is None or not r["dirty"] or r["sync_state"] in tf.HELD_STATES or r["sha256"] != s["sha256"]
                    or bool(r["deleted"]) != (s["op"] == "del")):
                return dropped()  # changed since: the unit goes again as it is now
        space_vcs.cs_set(ctx.account, ctx.space, cs["cs_id"], state="pushing")
    items: list[OutgoingItem] = []
    for s in snaps:
        r = rows[(str(s["folder"]), str(s["relpath"]))]
        base = int(r["base_seq"]) if r is not None else int(s.get("base_seq") or 0)
        if s["op"] == "del":
            items.append(OutgoingItem(kind=TREE, key=s["key"], unit=s["unit"], op="del", base_seq=base,
                                      meta=_tree_meta(s)))
            continue
        path = _tree_path(ctx, s)
        copy = tf.snapshot(*a, cs["cs_id"], str(s["key"]), path, str(s["sha256"])) if path else ""
        if not copy:
            return dropped()  # changed since the scan: the unit goes again as it is now
        items.append(OutgoingItem(kind=TREE, key=s["key"], unit=s["unit"], op="put", base_seq=base,
                                  sha256=str(s["sha256"]), size=int(s["size"]), path=copy, meta=_tree_meta(s)))
    if not items:
        return dropped()
    units = sorted({i.unit for i in items})
    fences = {lk["unit"]: int(lk["fence"]) for lk in space_vcs.locks(*a, now=now) if lk["mine"] and lk["unit"] in units}
    head = space_vcs.head_get(*a)
    out = OutgoingChangeset(cs_id=cs["cs_id"], base_seq=int(head["seq"]), items=tuple(items), device=device_id(),
                            group=cs["group_id"], part=int(cs["part"]), last=bool(cs["last"]), message=cs["message"],
                            actor=cs["actor"], ducky=cs["ducky"], fences=fences, author=str(cs.get("author") or ""),
                            author_pc=str(cs.get("author_pc") or ""))
    res = _call(ctx.backend.push, ctx.space, ctx.plugin, out)
    if isinstance(res, (Committed, Replay, Conflict, Locked, Refused)):
        tf.drop_snapshots(*a, cs["cs_id"])  # answered for good: no retry sends them again
    named = _units_of(snaps)
    if isinstance(res, (Committed, Replay)):
        _tree_sent(ctx, snaps, res.seq)
        space_vcs.mark_committed(*a, res.seq, [], units=units)
        space_vcs.cs_set(ctx.account, ctx.space, cs["cs_id"], state="committed", seq=res.seq)
        space_vcs.head_put(*a, moves=0)
        space_vcs.unit_tries_clear(*a, [_unit_key(fid, unit) for fid, unit in named.values()])
        return _Out("committed", seq=res.seq)
    if isinstance(res, Conflict):
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        return _Out("conflict")
    if isinstance(res, Locked):
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        for lk in res.units:
            _cache_lock(ctx, lk, now)
            if lk.unit in named:
                fid, unit = named[lk.unit]
                ff.unit_set_state(*a, fid, unit, "quarantined",
                                  f"Changed here while "
                                  f"{holder(ctx.account, ctx.space, lk.user, lk.user_name, lk.device)[0]} holds it")
        return _Out("locked")
    if isinstance(res, Refused):
        space_vcs.cs_delete(ctx.account, ctx.space, cs["cs_id"])
        unit_of_key = {str(s["key"]): str(s["unit"]) for s in snaps}
        for it in res.items:
            if unit_of_key.get(it.key) in named:
                fid, unit = named[unit_of_key[it.key]]
                ff.unit_set_state(*a, fid, unit, "refused", it.reason)
        return _Out("refused")
    if isinstance(res, Missing):
        return _Out("missing", error=reason_of(res), sent=snaps)
    if isinstance(res, Error) and fresh and not _later_part(cs):
        return _Out("failed", error=reason_of(res), sent=snaps)
    return _stop(ctx, res, now=now)


@dataclass
class _UnitIn:
    """One unit of a pull: its files in order, the seq it lands at, who changed it."""

    folder: tf.Folder
    project_id: str
    unit: str
    uid: str
    seq: int = 0
    by: str = ""
    at: float = 0.0
    items: list[tf.Incoming] = field(default_factory=list)
    keys: list[list[Any]] = field(default_factory=list)  # [key, seq, op, relpath]: what a conflict holds


def _rel_ok(rel: str) -> bool:
    return bool(rel) and not rel.startswith("/") and "\\" not in rel and ":" not in rel and all(
        p and p not in (".", "..") for p in rel.split("/"))


def _land(got: IncomingItem, temp: Path) -> str:
    """A download into ``temp`` (beside its target, so the apply is a rename). Returns the
    sha256 of what landed, streamed."""
    tp = tf._lp(temp)
    if got.data is not None:
        with open(tp, "wb") as f:
            f.write(got.data)
    else:
        try:
            os.replace(got.path, tp)
        except OSError:  # another volume
            shutil.copyfile(got.path, tp)
            Path(got.path).unlink(missing_ok=True)
    return tf._sha_file(temp)


def _held_items(c: Mapping[str, Any] | None) -> list[list[Any]]:
    """What a folder unit's conflict holds of theirs: ``[key, seq, op, relpath]`` each."""
    if not c or not c["mine_value"]:
        return []
    try:
        held = json.loads(c["mine_value"]).get("held") or []
    except (ValueError, AttributeError):
        return []
    return [list(h) for h in held if isinstance(h, list) and len(h) == 4]


def _hold_unit(conn: Any, ctx: Ctx, u: _UnitIn) -> None:
    """Theirs is held: the unit changed here, so nothing on disk changes. The conflict keeps
    what theirs is (newest seq per file) for Keep mine, Take theirs or Keep both. Mine is
    the unit as it is on disk; ``mine_value`` lists theirs (keys and seqs, no content)."""
    a = (ctx.account, ctx.space, ctx.plugin)
    key = _unit_key(u.folder.id, u.unit)
    held = {str(h[0]): h for h in _held_items(space_vcs.conflict_get(*a, TREE, key, conn=conn))}
    for k, seq, op, rel in u.keys:
        if k not in held or int(seq) >= int(held[k][1]):
            held[k] = [k, int(seq), op, rel]
    space_vcs.conflict_put(conn, *a, TREE, key, unit=u.uid, reason="changed",
                           mine_value=json.dumps({"held": sorted(held.values())}, separators=(",", ":")),
                           theirs_seq=max(int(h[1]) for h in held.values()), theirs_by=u.by, theirs_at=u.at)


def _apply_tree(ctx: Ctx, entries: list[tuple[IncomingItem, int, str, float]], *,
                force: bool = False, upto: int | None = None) -> dict[str, Any]:
    """Pulled folder files, unit by unit in seq order: each file's newest version is
    downloaded and verified into a temp file beside its target, then the unit lands whole
    (:func:`team_folders.apply_unit`: other files, ``last``, deletes; the origin's mtimes).
    A unit changed here is held as a conflict (or merges); a unit one of whose files can't
    be downloaded or verified doesn't land at all, and every file of it is set aside to be
    retried together (never applied in halves). A file this PC already has at that seq, or
    changed on top of it (its own commit coming back), is skipped. Names are this PC's: a
    file or unit named here with another case or Unicode form keeps its name here.
    ``force``: Take theirs over changes here. A unit is claimed at its seq (what lock
    requests say) only while nothing of it is set aside (:func:`space_vcs.unit_claim`);
    the landing that makes it whole again claims it at ``upto``, the seq every changeset is
    applied up to once this lands (claims skipped meanwhile, this PC's own commits too,
    are at or below it), else at its seq. Returns ``{state, units, conflicts, blocked}``
    (``units``: unit ids that changed here)."""
    a = (ctx.account, ctx.space, ctx.plugin)
    latest: dict[str, tuple[IncomingItem, int, str, float]] = {}
    for it, seq, by, at in sorted(entries, key=lambda e: e[1]):
        latest.pop(it.key, None)
        latest[it.key] = (it, seq, by, at)
    units: dict[tuple[str, str, str], _UnitIn] = {}
    seen: list[tuple[str, int]] = []  # (unit id, seq) applied with nothing to do here
    blocked: list[tuple[IncomingItem, int, str]] = []
    failed: dict[str, str] = {}  # unit id → why one of its files can't land now
    named: dict[tuple[str, str], dict[str, str]] = {}

    def local_unit(folder: tf.Folder, pid: str, unit: str) -> str:
        """A unit as this PC names it."""
        names = named.get((folder.id, pid))
        if names is None:
            names = named[(folder.id, pid)] = {tf.norm_name(r["unit"]): r["unit"]
                                               for r in ff.files(*a, folder.id, project_id=pid)}
        return names.get(tf.norm_name(unit), unit)

    def unit_in(folder: tf.Folder, pid: str, unit: str, seq: int, by: str, at: float) -> _UnitIn:
        u = units.get((folder.id, pid, tf.norm_name(unit)))
        if u is None:
            u = units[(folder.id, pid, tf.norm_name(unit))] = _UnitIn(
                folder, pid, unit, tf.tree_unit(ctx.backend, ctx.space, folder.id, unit))
        u.seq, u.by, u.at = max(u.seq, seq), by or u.by, at or u.at
        return u

    def here(row: Mapping[str, Any] | None, seq: int) -> bool:
        """This version, or a newer one, is here already: a clean file at or past it, or one
        changed here on top of it (as for docs)."""
        return (row is not None and row["sync_state"] != "refetch" and int(row["base_seq"]) >= seq
                and (bool(row["dirty"]) or not force))

    def fail(it: IncomingItem, folder: tf.Folder, row: Mapping[str, Any] | None, seq: int, why: str) -> None:
        blocked.append((it, seq, why))
        for uid in {it.unit, tf.tree_unit(ctx.backend, ctx.space, folder.id, str(row["unit"])) if row else ""} - {""}:
            failed.setdefault(uid, why)

    def gone(it: IncomingItem, folder: tf.Folder, seq: int, by: str, at: float) -> None:
        row = ff.by_key(*a, it.key)
        if row is None or here(row, seq):  # unknown here, or made again here on top of this delete
            seen.append((it.unit, seq))
            return
        pid = str(row["project_id"])
        u = unit_in(folder, pid, str(row["unit"]), seq, by, at)
        u.items.append(tf.Incoming(relpath=tf._bound_rel(row["relpath"], pid), op="del", item_key=it.key))
        u.keys.append([it.key, seq, "del", row["relpath"]])

    todo: list[tuple[IncomingItem, int, str, float, tf.Folder, dict[str, Any] | None]] = []
    for it, seq, by, at in latest.values():
        folder = tf.folder_of(ctx.plugin, _tree_folder_id(it.key))
        if folder is None:
            seen.append((it.unit, seq))  # a folder the plugin no longer declares
            continue
        row = ff.by_key(*a, it.key)
        if here(row, seq):
            seen.append((it.unit, seq))
            continue
        if it.op == "del":
            gone(it, folder, seq, by, at)
        else:
            todo.append((it, seq, by, at, folder, row))
    fetched = _download(ctx, [it for it, *_ in todo])
    if any(isinstance(g, Offline) for g in fetched.values()):
        for g in fetched.values():
            _drop_temp(g)
        return {"state": "offline", "error": reason_of(next(g for g in fetched.values() if isinstance(g, Offline)))}
    for it, seq, by, at, folder, row in todo:
        got = fetched.get(it.id)
        if not isinstance(got, IncomingItem):
            fail(it, folder, row, seq, reason_of(got) or "The download failed.")
            continue
        if got.op == "del":  # deleted since (a set-aside file fetched again)
            gone(it, folder, int(got.seq or seq), got.by or by, got.at or at)
            continue
        meta = dict(got.meta or it.meta or {})
        stored = str(meta.get("relpath") or "")
        pid = stored.split("/", 1)[0] if folder.project and "/" in stored else ""
        rel = tf._bound_rel(stored, pid)
        if not _rel_ok(stored) or (folder.project and not pid):
            _drop_temp(got)
            fail(it, folder, row, seq, "This file's name can't be used on this PC.")
            continue
        if row is not None and str(row["project_id"]) == pid:
            rel, unit = tf._bound_rel(row["relpath"], pid), str(row["unit"])  # its name here
        else:
            theirs = tf.unit_of(folder, rel, pid)
            unit = local_unit(folder, pid, theirs)
            if unit != theirs and folder.unit == "*":  # a unit named here with another case
                rel = tf._unit_local(unit, pid) + rel[len(tf._unit_local(theirs, pid)):]
        stored = tf._stored(rel, pid)
        if tf.matches(folder.ignore, rel):
            _drop_temp(got)
            seen.append((it.unit, seq))
            continue
        try:
            temp = tf.temp_for(*a, folder, rel, pid)
            sha = _land(got, temp)
        except OSError as exc:
            _drop_temp(got)
            fail(it, folder, row, seq, _why(exc))
            continue
        want = got.sha256 or it.sha256
        if want and want != sha:
            Path(tf._lp(temp)).unlink(missing_ok=True)
            fail(it, folder, row, seq, "The download didn't match what was sent.")
            continue
        u = unit_in(folder, pid, unit, int(got.seq or seq), got.by or by, got.at or at)
        u.items.append(tf.Incoming(relpath=rel, op="put", temp=str(temp), mtime_ns=int(meta.get("mtime_ns") or 0),
                                   sha256=sha, size=os.stat(tf._lp(temp)).st_size, item_key=it.key))
        u.keys.append([it.key, int(got.seq or seq), "put", stored])
    applied: list[str] = []
    conflicts = held_back = 0
    for u in units.values():
        why = failed.get(u.uid)
        if why is not None:
            # A file of the unit didn't arrive whole: none of it lands; all of it is retried together.
            for item in u.items:
                if item.temp:
                    Path(tf._lp(item.temp)).unlink(missing_ok=True)
            conn = db.connect()
            with db.write_txn(conn):
                for k, seq, _op, _rel in u.keys:
                    space_vcs.blocked_put(conn, *a, TREE, k, unit=u.uid, seq=int(seq), error=why)
            held_back += 1
            continue
        foreign = space_vcs.foreign_lock(*a, u.uid)
        try:
            res = tf.apply_unit(*a, u.folder, u.unit, u.items, seq=u.seq, project_id=u.project_id,
                                held=bool(foreign) and u.folder.on_locked == "readonly", force=force)
        finally:
            for item in u.items:
                if item.temp:
                    Path(tf._lp(item.temp)).unlink(missing_ok=True)
        conn = db.connect()
        with db.write_txn(conn):
            if res.get("ok"):
                was_aside = space_vcs.unit_set_aside(conn, *a, u.uid)
                for k, *_ in u.keys:
                    space_vcs.blocked_delete(conn, *a, TREE, k)
                space_vcs.unit_claim(conn, *a, u.uid, upto if was_aside and upto is not None else u.seq)
                if force:
                    space_vcs.conflict_delete(conn, *a, TREE, _unit_key(u.folder.id, u.unit))
                applied.append(u.uid)
            elif res.get("state") == "conflict":
                _hold_unit(conn, ctx, u)
                conflicts += 1
            else:
                held_back += 1
                for k, seq, _op, _rel in u.keys:
                    space_vcs.blocked_put(conn, *a, TREE, k, unit=u.uid, seq=int(seq),
                                          error=str(res.get("error") or "The unit couldn't be applied."))
    conn = db.connect()
    with db.write_txn(conn):
        for unit_id, seq in seen:
            if unit_id not in failed:
                space_vcs.unit_claim(conn, *a, unit_id, seq)
        for it, seq, why in blocked:
            space_vcs.blocked_put(conn, *a, TREE, it.key, unit=it.unit, seq=seq, error=why)
    return {"state": "ok", "units": applied, "conflicts": conflicts, "blocked": len(blocked) + held_back}


def _wait_behind_update(ctx: Ctx, waiting: list[IncomingChangeset]) -> None:
    """What waits behind "Update now" (the bar says how many and from whom), and its units
    read-only so nobody edits a stale base. An empty list clears it."""
    a = (ctx.account, ctx.space, ctx.plugin)
    before = space_vcs.incoming_queue_get(*a)
    space_vcs.incoming_queue_put(*a, count=len(waiting), by=[author(ctx.account, ctx.space, cs.by, cs.device, cs.actor, cs.ducky) for cs in waiting],
                                 units=[i.unit for cs in waiting for i in cs.items if i.unit],
                                 seq=max((cs.seq for cs in waiting), default=0))
    if before["count"] or waiting:
        _protect(ctx)


def _refetch(ctx: Ctx) -> dict[str, Any]:
    """Folder files to fetch again at the team's head (Discard mine, Take theirs over a file
    only this PC changed). A file the team no longer has just goes."""
    a = (ctx.account, ctx.space, ctx.plugin)
    rows = tf.refetch_items(*a)
    entries: list[tuple[IncomingItem, int, str, float]] = []
    for r in rows:
        folder = tf.folder_of(ctx.plugin, r["folder_id"])
        if folder is None:
            continue
        key = r["item_key"] or tf.tree_key(ctx.backend, ctx.space, folder.id, r["unit"], r["relpath"])
        got = _call(ctx.backend.fetch, ctx.space, ctx.plugin, TREE, key, None)
        if isinstance(got, Offline):
            for it, *_ in entries:
                _drop_temp(it)
            return {"state": "ok", "units": [], "conflicts": 0}  # the next round tries again
        if not isinstance(got, IncomingItem):
            conn = db.connect()
            with db.write_txn(conn):
                ff.file_delete(conn, *a, folder.id, r["relpath"])
            continue
        entries.append((got, int(got.seq), got.by, float(got.at)))
    if not entries:
        return {"state": "ok", "units": [], "conflicts": 0}
    return _apply_tree(ctx, entries)


def after_pull(plugin: str) -> None:
    """A pull changed the files of a plugin that keeps its own folders. ``reload``: its
    reload epoch moves on, so every process that loaded it reloads it: the app restarts it
    once none of its calls is running (:func:`host.reload_moved_plugins`, which then
    refreshes its panels), an MCP bridge before its next call. ``notify``: its open panels
    hear it."""
    if any(f.after_pull == "reload" for f in tf.plugin_folders(plugin)):
        ff.reload_bump(plugin, "pull")
        return
    _tell_panels(plugin, switched=False)


def _tell_panels(plugin: str, *, switched: bool) -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "plugin_scope_changed", "plugins": [plugin], **({"switched": True} if switched else {})})
    except Exception:  # noqa: BLE001 — the next status read shows it
        _log.debug("telling %s's panels failed", plugin, exc_info=True)


def apply_waiting(plugin: str, *, wait: float = CALL_APPLY_WAIT_S) -> dict[str, Any]:
    """Before an agent's call: changes waiting behind "Update now" for ``plugin`` land first
    (the agent never works on a stale base). Its reload epoch moves on, so the call's own
    process reloads the plugin before it runs (the caller checks); the app restarts it."""
    scope = scopes.active_scope(plugin)
    if scope["kind"] != "shared":
        return {"ok": True, "applied": 0}
    account, space = scope["account"], scope["id"]
    if not space_vcs.incoming_queue_get(account, space, plugin)["count"]:
        return {"ok": True, "applied": 0}
    from backend.uefn_plugins.spaces import ledger_for

    backend = ledger_for(account, space, plugin)
    if backend is None:
        return {"ok": False, "applied": 0}
    out = run(account, space, plugin, backend, push=False, pull=True, force=True, lock_wait=wait)
    if out.get("tree"):
        after_pull(plugin)
    return {"ok": out["state"] == "ok", "applied": int(out.get("tree") or 0), "error": out.get("error", "")}


def folder_work(account: str, space: str, plugin: str) -> bool:
    """Something for a round to do in the plugin's folder copies: changed units not held
    back (nor waiting after a failed try), files to fetch again, or changes waiting behind
    Update now that may land now."""
    if not tf.plugin_folders(plugin):
        return False
    tries, now = space_vcs.unit_tries(account, space, plugin), time.time()
    if any(not info["states"] & set(tf.HELD_STATES)
           and float((tries.get(_unit_key(fid, unit)) or {}).get("next_try") or 0.0) <= now
           for (fid, unit), info in ff.dirty_units(account, space, plugin).items()):
        return True
    return folder_due(account, space, plugin)


def folder_due(account: str, space: str, plugin: str) -> bool:
    """A round should come now for the plugin's folders even with nothing quiet to send:
    files to fetch again (Discard mine, Take theirs), or changes that waited behind Update
    now and may land now (its panel was hidden)."""
    if tf.refetch_items(account, space, plugin):
        return True
    return bool(space_vcs.incoming_queue_get(account, space, plugin)["count"]) and not _holds_incoming(
        Ctx(account, space, plugin, None))


def _local_unsent(account: str, space: str, plugin: str) -> list[str]:
    """Units with changes not sent and not held back (a conflict, a quarantine, refused)."""
    return sorted(f"{fid}/{unit}" for (fid, unit), info in ff.dirty_units(account, space, plugin).items()
                  if not info["states"] & set(tf.HELD_STATES))


def pull_copy(account: str, space: str, plugin: str, backend: Any, *, label: str = "",
              timeout: float = SWITCH_PULL_S, landed: dict[str, int] | None = None) -> tuple[bool, str]:
    """A switch to a team: the team's copy of the plugin (its folders too) comes down to the
    head first, while the plugin still reads the copy it uses now. The bar shows "Getting
    <team>'s files… 40%". ``(False, why)`` when it didn't finish: nothing changes then.
    ``landed["tree"]`` adds up the folder units it changed here (also when it stops part way)."""
    label = label or repo.sync_get(account, space)["label"] or "The team"
    deadline = time.monotonic() + max(1.0, timeout)
    try:
        space_vcs.binding_put(plugin, label, 0)
        seen = refresh(account, space, [plugin], backend)
        if seen["state"] not in ("ok",):
            return False, f"{label}'s files didn't arrive ({seen.get('error') or seen['state']}). Nothing changed."
        start = int(space_vcs.head_get(account, space, plugin)["seq"])

        def progress(seq: int, head: int) -> None:
            span = max(1, int(head) - start)
            space_vcs.binding_put(plugin, label, int(100 * max(0, int(seq) - start) / span))

        while True:
            out = run(account, space, plugin, backend, push=False, pull=True, force=True, progress=progress)
            if landed is not None:
                landed["tree"] = int(landed.get("tree") or 0) + int(out.get("tree") or 0)
            if out["state"] != "ok":
                return False, f"{label}'s files didn't arrive ({out.get('error') or out['state']}). Nothing changed."
            head = space_vcs.head_get(account, space, plugin)
            if int(head["seq"]) >= int(head["remote_seq"]):
                break
            if time.monotonic() >= deadline:
                return False, f"Getting {label}'s files is taking too long. Nothing changed; try again."
        stuck = [b for b in space_vcs.blocked(account, space, plugin) if b["kind"] == TREE]
        if stuck:
            return False, f"Some of {label}'s files couldn't be downloaded ({stuck[0]['error']}). Nothing changed."
        return True, ""
    finally:
        space_vcs.binding_put(plugin, None)


def send_copy(account: str, space: str, plugin: str, backend: Any, *, label: str = "",
              timeout: float = SWITCH_PULL_S) -> tuple[bool, str]:
    """The first share: the units just copied into the team's copy go out now (every changed
    unit, not only quiet ones). ``(False, why)`` when some didn't."""
    label = label or repo.sync_get(account, space)["label"] or "the team"
    out = run(account, space, plugin, backend, push=True, pull=False, force=True, quiet_s=0.0,
              lock_wait=min(timeout, 600.0))
    left = _local_unsent(account, space, plugin)
    if out["state"] != "ok" or left:
        why = out.get("error") or (f"{len(left)} not sent" if left else out["state"])
        return False, f"Your files didn't reach {label} ({why}). Nothing changed."
    return True, ""


def send_before_leaving(account: str, space: str, plugin: str, backend: Any, *,
                        timeout: float = LEAVE_SEND_S) -> list[str]:
    """A switch away from a team copy: its changed units go out first, quiet or not (a save
    in progress is waited for), for at most ``timeout`` seconds. Returns the units still
    not sent (they stay in that copy)."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        run(account, space, plugin, backend, push=True, force=True, quiet_s=0.0,
            lock_wait=max(1.0, deadline - time.monotonic()))
        unsent = _local_unsent(account, space, plugin)
        if not unsent or time.monotonic() >= deadline:
            return unsent
        time.sleep(0.5)


def resolve_unit(account: str, space: str, plugin: str, backend: Any, folder_id: str, unit: str,
                 choice: str) -> dict[str, Any]:
    """Settle a folder unit. A conflict (changed here and by the team): ``mine`` (Keep mine:
    the unit as it is here goes out on top of theirs), ``theirs`` (Take theirs: the team's
    version replaces it), ``both`` (Keep both: mine is kept beside it as ``<unit> (<name>'s
    copy)``, a unit of its own, then theirs lands). Theirs is downloaded first: until it is
    here, and while it hasn't landed, nothing changes and the conflict stays (``ok`` false
    with the reason). Changed while someone else holds it (quarantined): ``keep_copy``
    (Keep mine as a copy, then the team's version comes back), ``discard`` (Discard mine),
    or ``mine`` once their lock has gone."""
    folder = tf.folder_of(plugin, folder_id)
    if folder is None:
        raise ValueError(f"{folder_id} isn't one of this plugin's folders.")
    pid = unit.split("/", 1)[0] if folder.project else ""
    a = (account, space, plugin)
    key = _unit_key(folder.id, unit)
    c = space_vcs.conflict_get(*a, TREE, key)
    me = _my_name()
    if choice == "keep_copy":
        out = tf.keep_mine_as_copy(*a, folder, unit, project_id=pid, holder=me)
    elif choice == "discard":
        out = tf.discard_mine(*a, folder, unit, project_id=pid)
    elif choice == "mine":
        held = space_vcs.foreign_lock(*a, tf.tree_unit(backend, space, folder.id, unit))
        if held and c is None:
            who, pc = holder_of(account, space, held)
            if pc:
                return {"ok": False, "error": f"It's still open on your other PC, {pc}. "
                                              "Close it there (or release it) first."}
            return {"ok": False, "error": f"{_who(who)} still holds it. Ask them to release it first."}
        out = _keep_mine(account, space, plugin, folder, unit, pid, _held_items(c), conflict=c is not None)
    elif choice in ("theirs", "both"):
        if backend is None:
            return {"ok": False, "error": "Not connected: this space can't be reached right now."}
        ctx = Ctx(account, space, plugin, backend)
        entries, why = _fetch_theirs(ctx, _held_items(c))  # theirs first: nothing here changes without it
        if why:
            return {"ok": False, "error": why}
        if choice == "both":
            try:
                tf.keep_mine_as_copy(*a, folder, unit, project_id=pid, holder=me)
            except BaseException:
                _drop_entries(entries)
                raise
        out = _take_theirs(ctx, folder, unit, pid, entries)
    else:
        raise ValueError("Choose mine, theirs, both, keep_copy or discard.")
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "That didn't work. The conflict is still there.")}
    conn = db.connect()
    with db.write_txn(conn):
        space_vcs.conflict_delete(conn, *a, TREE, key)
    return {"ok": True, **{k: v for k, v in dict(out).items() if k != "ok"}}


def _my_name() -> str:
    try:
        from frontend.duckyos_account import get_status

        st = get_status()
        return str(st.get("display_name") or str(st.get("email") or "").split("@", 1)[0] or "")
    except Exception:  # noqa: BLE001
        return ""


def _keep_mine(account: str, space: str, plugin: str, folder: tf.Folder, unit: str, pid: str,
               held: list[list[Any]], *, conflict: bool) -> dict[str, Any]:
    """Keep mine. After a conflict the unit as it is here goes out on top of theirs: each file
    they changed goes again from here based on their seq (so the server takes it), a file
    only they have goes as a delete. A quarantined unit (their lock is gone) just goes."""
    a = (account, space, plugin)
    seqs = {str(h[0]): int(h[1]) for h in held}
    rows = ff.files(*a, folder.id, unit=unit)
    by_key = {r["item_key"]: r for r in rows if r["item_key"]}
    now = time.time()
    conn = db.connect()
    with db.write_txn(conn):
        for r in rows:
            if r["sync_state"] == "refetch":
                continue
            if conflict and r["item_key"] in seqs:
                ff.file_put(conn, *a, folder.id, r["relpath"], dirty=True,
                            base_seq=max(int(r["base_seq"]), seqs[r["item_key"]]), sync_state="", sync_error="",
                            changed_at=now)
            elif r["dirty"]:
                ff.file_put(conn, *a, folder.id, r["relpath"], sync_state="", sync_error="")
        for k, seq, op, rel in held:
            if op == "put" and k not in by_key and _rel_ok(str(rel)):
                ff.file_put(conn, *a, folder.id, str(rel), unit=unit, project_id=pid, dirty=True, deleted=True,
                            base_seq=int(seq), sha256="", size=0, mtime_ns=0, changed_at=now, item_key=k,
                            sync_state="", sync_error="")
        # The unit isn't held back any more (its clean files carried the conflict too).
        conn.execute("UPDATE folder_files SET sync_state='', sync_error='' WHERE account_id=? AND space_id=? AND "
                     "plugin_id=? AND folder_id=? AND unit=? AND sync_state IN ('conflict', 'quarantined', "
                     "'locked_out')", (*a, folder.id, unit))
    return {"ok": True, "kept": len(rows)}


def _fetch_theirs(ctx: Ctx, held: list[list[Any]]) -> tuple[list[tuple[IncomingItem, int, str, float]], str]:
    """What a folder unit's conflict holds of theirs, each file's current version
    downloaded first: ``(entries, '')``, or ``([], why)`` when one of them can't be."""
    entries: list[tuple[IncomingItem, int, str, float]] = []
    for k, seq, op, _rel in held:
        if op == "del":
            entries.append((IncomingItem(kind=TREE, key=str(k), op="del", seq=int(seq)), int(seq), "", 0.0))
            continue
        got = _call(ctx.backend.fetch, ctx.space, ctx.plugin, TREE, str(k), None)
        if not isinstance(got, IncomingItem):
            _drop_entries(entries)
            return [], f"The team's version couldn't be downloaded ({reason_of(got) or 'no answer'}). Nothing changed."
        entries.append((got, int(got.seq or seq), got.by, float(got.at)))
    return entries, ""


def _drop_entries(entries: Iterable[tuple[IncomingItem, int, str, float]]) -> None:
    for it, *_ in entries:
        _drop_temp(it)


def _take_theirs(ctx: Ctx, folder: tf.Folder, unit: str, pid: str,
                 entries: list[tuple[IncomingItem, int, str, float]]) -> dict[str, Any]:
    """Take theirs (``entries``: :func:`_fetch_theirs`): the team's version of each held file
    lands over this PC's changes; files only this PC changed come back at the team's head
    (or go). Not ``ok`` while theirs didn't land (the conflict stays)."""
    if not entries:  # nothing of theirs held (a broken lock): the team's version comes back
        tf.discard_mine(ctx.account, ctx.space, ctx.plugin, folder, unit, project_id=pid)
        return {"ok": True}
    with _lock(ctx.account, ctx.space, 30.0) as held:
        if not held:
            _drop_entries(entries)
            return {"ok": False, "error": "A sync of this space is running. Try again in a moment."}
        out = _apply_tree(ctx, entries, force=True)
    if out["state"] != "ok":
        return {"ok": False, "error": out.get("error", "")}
    uid = tf.tree_unit(ctx.backend, ctx.space, folder.id, unit)
    if uid not in out["units"]:
        stuck = next((b for b in space_vcs.blocked(ctx.account, ctx.space, ctx.plugin) if b["unit"] == uid), None)
        if stuck is not None or out["conflicts"]:
            return {"ok": False, "error": (stuck or {}).get("error") or "The team's version couldn't land. Try again."}
        tf.discard_mine(ctx.account, ctx.space, ctx.plugin, folder, unit, project_id=pid)  # nothing of theirs left
    return {"ok": True}


def folder_rows(account: str, space: str, plugin: str, backend: Any = None) -> list[dict[str, Any]]:
    """The bar's folder units: ``folder_id``, ``unit``, ``unitId`` (what its lock goes by),
    ``name``, ``state`` (``up_to_date``, ``changed_here``, ``locked_by``, ``conflict``,
    ``incoming``, ``quarantined``, ``refused``), ``holder``, ``mine`` (this PC holds its
    lock), ``files``, ``bytes`` and ``reason``."""
    ctx = Ctx(account, space, plugin, backend)
    waiting = set(space_vcs.incoming_queue_get(account, space, plugin)["units"])
    mine = {lk["unit"] for lk in space_vcs.locks(account, space, plugin) if lk["mine"]}
    names = {"synced": "up_to_date", "changed": "changed_here", "locked": "locked_by"}
    out: list[dict[str, Any]] = []
    for folder, pid in _folder_copies(ctx):
        uid = _uids(ctx, folder)
        for u in tf.units_table(account, space, plugin, folder, project_id=pid, unit_id=uid):
            state = names.get(u["state"], u["state"])
            if state in ("up_to_date", "changed_here") and uid(u["unit"]) in waiting:
                state = "incoming"
            out.append({"folder_id": folder.id, "unit": u["unit"], "unitId": uid(u["unit"]),
                        "name": _unit_display(folder, u["unit"]), "state": state, "holder": u.get("holder", ""),
                        "elsewhere": u.get("elsewhere", ""), "mine": uid(u["unit"]) in mine, "files": u["files"], "bytes": u["bytes"],
                        "reason": u["reason"]})
    return out


def folder_outgoing(account: str, space: str, plugin: str) -> list[dict[str, Any]]:
    """Folder units with changes to send (the drawer's outgoing list), each with its state:
    ``sending``, ``queued``, or why it is held back."""
    going = {(str(i.get("folder")), str(i.get("unit_name"))) for cs in space_vcs.cs_open(account, space, plugin)
             for i in cs["items"] if i.get("kind") == TREE}
    out: list[dict[str, Any]] = []
    tries = space_vcs.unit_tries(account, space, plugin)
    for (fid, unit), info in sorted(ff.dirty_units(account, space, plugin).items()):
        folder = tf.folder_of(plugin, fid)
        held = next((s for s in tf.HELD_STATES if s in info["states"]), "")
        tried = tries.get(_unit_key(fid, unit)) or {}
        reason = str(tried.get("error") or "")  # waiting after a failed try: says why
        if held:
            reason = next((r["sync_error"] for r in ff.files(account, space, plugin, fid, unit=unit)
                           if r["sync_state"] == held), "")
        out.append({"kind": TREE, "key": _unit_key(fid, unit), "name": _unit_display(folder, unit) if folder else unit,
                    "folder_id": fid, "unit": unit, "files": info["files"],
                    "state": held or ("sending" if (fid, unit) in going else "queued"), "reason": reason,
                    "attempts": int(tried.get("attempts") or 0)})
    return out


def migrate_folder_links() -> list[str]:
    """Once at start: a plugin that keeps its own folders and is linked to a team whose
    folder copy this PC never pulled or sent goes back on Local, and its bar offers "Copy
    Local into <team>" (the units the person picks). Returns the plugins moved."""
    account = scopes.account_id()
    if account == scopes.LOCAL:
        return []
    moved: list[str] = []
    for plugin, space in sorted(repo.links(account).items()):
        if not scopes.valid_space_id(space) or not tf.plugin_folders(plugin):
            continue
        try:
            if tf._migrate_link(plugin, scopes.shared_scope(account, space)):
                moved.append(plugin)
        except Exception:  # noqa: BLE001 — the plugin's bind before load tries again
            _log.warning("moving %s back to Local failed", plugin, exc_info=True)
    return moved


# --------------------------------------------------------------------------- rounds


def held_reason(account: str, space: str) -> str:
    """Why nothing may move in the space now (reset, lost, unavailable, paused), or ''."""
    st = repo.sync_get(account, space)
    why = HELD_STATES.get(str(st["state"] or ""))
    if why is None:
        return ""
    return str(st["error"] or why) if st["state"] == "unavailable" else why


def run(account: str, space: str, plugin: str, backend: Any, *, push: bool | None = None, pull: bool | None = None,
        force: bool = False, message: str = "", now: float | None = None, quiet_s: float | None = None,
        lock_wait: float | None = None, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """One round for (space, plugin): pull, then push. In Manual mode nothing moves unless
    asked (``push=True`` / ``pull=True``, with an optional ``message`` for the push). A
    round backing off is skipped unless ``force``; nothing moves while the space is held
    (:func:`held_reason`). The plugin's first round on the new sync reconciles this PC's
    copy first (:func:`_reconcile_v1`). Returns ``{state, changed, keys, error, pushed, pulled,
    conflicts, tree}`` (``changed``: ``[plugin]`` when a pull changed data the plugin reads;
    ``keys``: those doc keys and file paths, never this PC's own items; ``tree``:
    folder units a pull changed here, with ``reload`` when the plugin restarts for them;
    ``queued``: changesets waiting behind Update now); ``busy`` when another round holds
    the space. ``pull=True`` (asked for) also lands what waits behind Update now.
    ``quiet_s``: folder units unchanged this long go out (3 s; 0 for every changed unit).
    ``lock_wait``: how long to wait for another round of the space (10 minutes when
    ``force``, else none). A round that raises is this plugin's error (it backs off), never
    the space's other plugins'."""
    now = time.time() if now is None else now
    ctx = Ctx(account, space, plugin, backend)
    head = space_vcs.head_get(account, space, plugin)
    manual = head["mode"] == "manual"
    do_pull = (not manual) if pull is None else bool(pull)
    do_push = (not manual) if push is None else bool(push)
    out: dict[str, Any] = {"state": "ok", "changed": [], "error": "", "pushed": 0, "pulled": 0, "conflicts": 0,
                           "tree": 0, "keys": []}
    held_by = held_reason(account, space)
    if held_by:
        return {**out, "state": repo.sync_get(account, space)["state"], "error": held_by}
    if not force and float(head["next_try"]) > now:
        return {**out, "state": head["state"], "error": head["error"], "skipped": True}
    wait = (600.0 if force else 0.0) if lock_wait is None else max(0.0, lock_wait)
    with _lock(account, space, wait) as held:
        if not held:
            return {**out, "state": "busy"}
        repo.sync_put(account, space, called_at=now)
        try:
            if space_vcs.head_get(account, space, plugin)["engine"] != "v2":
                if pull is False:  # a push-only round (the exit flush): the move waits for a full one
                    return {**out, "skipped": True}
                got = _reconcile_v1(ctx, now=now)
                _deliver(ctx, got.get("changed") or [])
                keys = _seen_keys(plugin, got.get("changed"))
                out.update(state=got["state"], error=got.get("error", ""), pulled=got.get("pulled", 0),
                           conflicts=got.get("conflicts", 0), changed=[plugin] if keys else [], keys=keys)
            if do_pull and out["state"] == "ok":
                got = _pull_round(ctx, now=now, apply_now=pull is True, progress=progress)
                keys = [*out["keys"], *(k for k in _seen_keys(plugin, got.get("changed")) if k not in out["keys"])]
                out.update(state=got["state"], error=got.get("error", ""), pulled=out["pulled"] + got.get("pulled", 0),
                           conflicts=out["conflicts"] + got.get("conflicts", 0), tree=int(got.get("tree") or 0),
                           queued=int(got.get("queued") or 0), keys=keys,
                           changed=[plugin] if keys or got.get("tree") or out["changed"] else [])
            if do_push and out["state"] == "ok":
                sent = _push_round(ctx, message=message, now=now, pull=pull is not False,
                                   quiet_s=tf.QUIET_S if quiet_s is None else quiet_s)
                out.update(state=sent["state"], error=sent.get("error", ""), pushed=sent.get("pushed", 0),
                           tree=out["tree"] + int(sent.get("tree") or 0))
                if sent.get("tree"):
                    out["changed"] = [plugin]
        except data_crypto.Locked as exc:
            return {**out, "state": "locked", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — this plugin's round failed; the others go on
            return {**out, **_round_raised(ctx, exc, now=now)}
    if out["state"] == "ok":
        space_vcs.head_ok(account, space, plugin, now=now)
        repo.sync_put(account, space, synced_at=now)
    if out["pushed"]:
        from backend.uefn_plugins import live

        live.committed(account, space, plugin)  # a live group pulls it now
    if out["tree"] and any(f.after_pull == "reload" for f in tf.plugin_folders(plugin)):
        out["reload"] = True
    return out


def _round_raised(ctx: Ctx, exc: Exception, *, now: float) -> dict[str, Any]:
    """A round of one plugin raised: it backs off with the error shown, and the provider
    plugin's errors get it with the traceback."""
    import traceback

    msg = f"Sync failed: {type(exc).__name__}: {exc}"
    _log.warning("sync of %s in %s failed", ctx.plugin, ctx.space, exc_info=True)
    stop = _stop(ctx, Error(msg[:500]), now=now)
    try:
        from backend.tools.panel.panel_plugin_check import record
        from backend.uefn_plugins.spaces import provider_plugin

        record("plugin_sync_error", provider_plugin(), msg, stack=traceback.format_exc()[-4000:])
    except Exception:  # noqa: BLE001 — reporting never raises from a round
        _log.debug("recording the sync failure failed", exc_info=True)
    return {"state": stop.state, "error": stop.error}


def set_mode(account: str, space: str, plugin: str, mode: str) -> dict[str, Any]:
    """Auto (rounds push and pull by themselves) or Manual (only Push and Pull do)."""
    if mode not in MODES:
        raise ValueError("Choose Auto or Manual.")
    space_vcs.head_put(account, space, plugin, mode=mode)
    return {"ok": True, "mode": mode}


def refresh(account: str, space: str, plugins: list[str], backend: Any, *, now: float | None = None) -> dict[str, Any]:
    """``heads``: each plugin's head (what is incoming), imports, live locks, the roster. A new
    epoch puts the space in ``reset`` (read-only until the person chooses). The new sync
    switched off on the server (``v2`` false) pauses these plugins (read-only, "Team sync
    paused") and only them: plugins still on the older sync go on."""
    now = time.time() if now is None else now
    asked_at = time.time()
    got = _call(backend.heads, space, list(plugins))
    if not isinstance(got, Heads):
        if isinstance(got, Paused):
            repo.sync_put(account, space, state="paused")
        return {"state": "paused" if isinstance(got, Paused) else "offline" if isinstance(got, Offline) else "error",
                "error": reason_of(got)}
    if not got.v2:
        for plugin in plugins:
            space_vcs.head_put(account, space, plugin, state="paused", error=SYNC_PAUSED)
        return {"state": "paused", "error": SYNC_PAUSED}
    known = {h["epoch"] for h in space_vcs.heads(account, space).values() if h["epoch"]}
    reset = bool(known) and got.epoch not in known
    if reset:
        space_vcs.reset_epoch(account, space, got.epoch)
        repo.sync_put(account, space, state="reset", error="This space's data was reset on the server.")
    elif repo.sync_get(account, space)["state"] == "paused":
        repo.sync_put(account, space, state="ok")
    incoming: dict[str, int] = {}
    for plugin in plugins:
        remote = int(got.heads.get(plugin, 0))
        head = space_vcs.head_get(account, space, plugin)
        fields: dict[str, Any] = {"remote_seq": remote, "epoch": got.epoch}
        if plugin in got.importing:
            fields.update(state="importing", error=f"Moving to the new sync ({int(got.importing[plugin])}%)")
        elif head["state"] in ("paused", "importing"):
            fields.update(state="ok", error="")  # back on (its next round says otherwise if not)
        space_vcs.head_put(account, space, plugin, **fields)
        incoming[plugin] = max(0, remote - int(head["seq"]))
    kept = _keep_roster(account, space, asked_at, got.people, got.usage, now=now)
    if kept is not None:  # an older answer's locks are as old as its roster
        _locks_from(account, space, got.locks, now=now, asked_at=asked_at)
        _roster_came(account, space, *kept)
    return {"state": "reset" if reset else "ok", "error": "", "incoming": incoming, "epoch": got.epoch,
            "importing": dict(got.importing)}


# --------------------------------------------------------------------------- locks


def _cache_lock(ctx: Ctx, lk: LockInfo, now: float, reason: str = "") -> None:
    space_vcs.lock_put(ctx.account, ctx.space, lk.plugin or ctx.plugin, lk.unit, name=lk.name, holder_user=lk.user,
                       holder_name=lk.user_name, holder_device=lk.device, since=lk.since, lease_left=lk.lease_left,
                       fence=lk.fence, mine=lk.mine, reason=reason or lk.reason, now=now)


def _tell_bars(plugins: Iterable[str]) -> None:
    """These plugins' bars read their status again now (who holds what, who is here): a
    status push, so their panels don't re-read any data."""
    names = sorted({str(p) for p in plugins if p})
    if not names:
        return
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "plugin_scope_changed", "plugins": names, "status": True})
    except Exception:  # noqa: BLE001 — the bar's next read shows it
        _log.debug("telling the bars failed", exc_info=True)


def _lock_marks(account: str, space: str) -> dict[str, frozenset[tuple[str, bool, str]]]:
    """Per plugin, which units are locked here and by whom: to tell its bar when that moves."""
    marks: dict[str, set[tuple[str, bool, str]]] = {}
    for lk in space_vcs.locks(account, space):
        marks.setdefault(str(lk["plugin_id"]), set()).add(
            (str(lk["unit"]), bool(lk["mine"]), str(lk.get("holder_user") or "")))
    return {p: frozenset(v) for p, v in marks.items()}


def _roster_marks(roster: Any) -> dict[str, frozenset[tuple[str, str, bool, tuple[str, ...], str]]]:
    """Per plugin, who is in it, what they edit and the live group they're in (a roster's
    people): to tell its bar."""
    marks: dict[str, set[tuple[str, str, bool, tuple[str, ...], str]]] = {}
    people = roster.get("people") if isinstance(roster, Mapping) else None
    for p in people if isinstance(people, list) else []:
        plugins = p.get("plugins") if isinstance(p, Mapping) else None
        if not isinstance(plugins, Mapping):
            continue
        for plugin, here in plugins.items():
            if not isinstance(here, Mapping):
                continue
            editing = tuple(sorted({str(e.get("unitName") or "") for e in here.get("editing") or []
                                    if isinstance(e, Mapping)}))
            live = here.get("live")
            room = str(live.get("room") or "") if isinstance(live, Mapping) else ""
            marks.setdefault(str(plugin), set()).add(
                (str(p.get("user") or ""), str(p.get("device") or ""), bool(here.get("viewing")), editing, room))
    return {p: frozenset(v) for p, v in marks.items()}


def _team_marks(roster: Any) -> frozenset[tuple[str, str, str]]:
    """Who is live in which group on the team copy and what they have on screen: to tell the
    title bar's Live menu."""
    marks: set[tuple[str, str, str]] = set()
    people = roster.get("people") if isinstance(roster, Mapping) else None
    for p in people if isinstance(people, list) else []:
        if not isinstance(p, Mapping):
            continue
        plugins = p.get("plugins") if isinstance(p.get("plugins"), Mapping) else {}
        rooms = {str(r["live"].get("room") or "") for r in plugins.values()
                 if isinstance(r, Mapping) and isinstance(r.get("live"), Mapping)}
        top = p.get("live")
        room = str(top.get("room") or "") if isinstance(top, Mapping) else ",".join(sorted(rooms - {""}))
        viewing = ",".join(sorted(str(k) for k, r in plugins.items() if isinstance(r, Mapping) and r.get("viewing")))
        if room:
            marks.add((str(p.get("device") or ""), room, viewing))
    return frozenset(marks)


def _moved(before: Mapping[str, Any], after: Mapping[str, Any]) -> set[str]:
    return {p for p in set(before) | set(after) if before.get(p) != after.get(p)}


def _roster_came(account: str, space: str, old: Any, roster: Mapping[str, Any]) -> None:
    """A fresh roster: the bars of plugins whose people moved read their status again (and
    the title bar's Live menu when who is live where moved), panels hear what teammates hold
    now, Live ends where everyone else left this PC's group (:func:`live.end_lone_groups`),
    a group of one this PC hosts joins a teammate's (:func:`live.merge_lone_groups`), and
    plugins that came to a team copy this PC is live on join it (:func:`live.catch_up`)."""
    _tell_bars(_moved(_roster_marks(old), _roster_marks(roster)))
    try:
        from backend.uefn_plugins import live

        if _team_marks(old) != _team_marks(roster):
            live._team_changed(account, space)
        live.roster_holds(account, space, roster)
        live.roster_invites(account, space, roster)
        live.end_lone_groups(account, space)
        live.merge_lone_groups(account, space)
        live.catch_up(account, space)
    except Exception:  # noqa: BLE001 — the next roster tells them
        _log.debug("teammates' holds failed", exc_info=True)


def _locks_from(account: str, space: str, live: Iterable[LockInfo], *, now: float,
                asked_at: float | None = None) -> dict[str, Any]:
    """The server's live locks replace the cache. Rows locked out by a lock that went are
    queued again; unsent edits under a lock of mine someone broke or took over become a
    conflict (kept, never pushed over theirs without the person), and the person is told
    about every lock of theirs that went."""
    rows = [asdict(lk) for lk in live]
    before = _lock_marks(account, space)
    change = space_vcs.locks_replace(account, space, rows, now=now, asked_at=asked_at)
    _tell_bars(_moved(before, _lock_marks(account, space)))
    for lk in change["freed"]:
        row = _row_of_unit(lk["unit"])
        if row:
            r = space_vcs.row(None, account, space, lk["plugin_id"], *row)
            if r and r["sync_state"] == "locked_out":
                repo.set_state(account, space, lk["plugin_id"], *row, "")
    for lk in change["lost"]:
        row = _row_of_unit(lk["unit"])
        r = space_vcs.row(None, account, space, lk["plugin_id"], *row) if row else None
        kept = bool(r and int(r["dirty"]))
        _tell_lock_lost(lk, kept=kept)
        if not kept:
            continue
        holder = person(lk.get("now_holder"))
        conn = db.connect()
        with db.write_txn(conn):
            space_vcs.conflict_put(conn, account, space, lk["plugin_id"], r["kind"], r["key"], unit=lk["unit"],
                                   reason="lock_broken", mine_value=r["value"], mine_sha=r["sha256"],
                                   mine_size=int(r["size"]), mine_deleted=bool(int(r["deleted"])),
                                   base_seq=int(r["rev"]), theirs_seq=int(r["rev"]), theirs_by=holder, theirs_at=now)
            space_vcs.set_row_state(conn, account, space, lk["plugin_id"], r["kind"], r["key"], "conflict",
                                    f"Your lock was taken by {holder}; your unsent change is kept.")
    return change


def _tell_lock_lost(lk: Mapping[str, Any], *, kept: bool) -> None:
    """A lock of mine went: someone broke it or took it over, or its lease ran out. An alert
    (a Windows notification when no Ducky window is in front) and the app hears it (its
    activity list, the plugin's bar)."""
    plugin, unit = str(lk["plugin_id"]), str(lk["unit"])
    name = str(lk.get("name") or _unit_name(unit))
    holder = _who(lk["now_holder"]) if lk.get("now_holder") else ""
    detail = f"{holder} took {name}" if holder else f"Your lock on {name} ended"
    detail += "; your unsent change is kept as a conflict." if kept else "."
    try:
        from backend.uefn_plugins.spaces import plugin_label
        from frontend.notifications import alert

        alert(plugin_label(plugin) or plugin, "Your lock was broken." if holder else "Your lock ended.",
              level="error", detail=detail)
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "plugin_lock_lost", "plugin_id": plugin, "unit": unit, "unit_name": name,
                       "holder": holder, "kept": kept, "detail": detail})
        push_ui_event({"type": "plugin_scope_changed", "plugins": [plugin], "status": True})
    except Exception:  # noqa: BLE001 — the bar's lock list says it on its next read
        _log.debug("telling about a lost lock failed", exc_info=True)


def _lock_answer(account: str, space: str, unit: str, res: Any) -> dict[str, Any]:
    if isinstance(res, (Granted, Held)):
        return {"unit": unit, "ok": True, "state": "granted" if isinstance(res, Granted) else "held",
                "fence": res.fence, "leaseLeft": res.lease_left}
    if isinstance(res, Behind):
        return {"unit": unit, "ok": False, "state": "behind", "seq": res.seq, "by": person(res.by) if res.by else "",
                "error": f"{_who(res.by)} changed this after your copy. Sync first."}
    if isinstance(res, LockInfo):
        who, pc = holder(account, space, res.user, res.user_name, res.device)
        error = f"It's open on your other PC, {pc}: locked there" if pc else f"Locked by {who}"
        return {"unit": unit, "ok": False, "state": "locked", "holder": who, "elsewhere": pc, "since": res.since,
                "leaseLeft": res.lease_left, "error": error}
    return {"unit": unit, "ok": False, "state": "error", "error": "No answer for this unit."}


def acquire(account: str, space: str, plugin: str, backend: Any, units: list[tuple[str, str]], *,
            reason: str = "manual", mode: str = "best", now: float | None = None) -> dict[str, dict[str, Any]]:
    """Lock ``units`` (``(unit, name)``) for this PC. Each says the seq of the last change to
    it applied here (:func:`space_vcs.unit_seqs`: never the plugin's head, which can be past
    a change of the unit set aside); a unit this PC is behind on is pulled (a download of it
    set aside is tried again now) and asked for once more (not while the space is held:
    :func:`held_reason`). ``reason``: ``auto`` (released 30 s after its last change
    went out, or once the plugin's tab closed: :func:`idle_auto_locks`), ``manual`` (kept until released), or '' to keep the reason each lock of mine
    already has (a renewal). Returns ``{unit: {ok, state, ...}}``."""
    now = time.time() if now is None else now
    ctx = Ctx(account, space, plugin, backend)
    asked = [LockUnit(u, n) for u, n in units]
    res = _call(backend.lock, space, plugin, asked, mode, space_vcs.unit_seqs(account, space, plugin,
                                                                               [u for u, _ in units]))
    if not isinstance(res, dict):
        return {u: {"unit": u, "ok": False, "state": "error", "error": reason_of(res)} for u, _ in units}
    behind = [lu for lu in asked if isinstance(res.get(lu.unit), Behind)]
    if behind and not held_reason(account, space):
        late = {lu.unit for lu in behind}
        for b in space_vcs.blocked(account, space, plugin):
            if b["unit"] in late and _retry_soon(b):
                space_vcs.blocked_retry_now(account, space, plugin, b["kind"], b["key"])
        with _lock(account, space, 30.0) as held:
            if held:
                _pull_round(ctx, now=now)
        again = _call(backend.lock, space, plugin, behind, mode,
                      space_vcs.unit_seqs(account, space, plugin, [lu.unit for lu in behind]))
        if isinstance(again, dict):
            res.update(again)
    out: dict[str, dict[str, Any]] = {}
    held_since = {lk["unit"]: float(lk["since"]) for lk in space_vcs.locks(account, space, plugin, now=now, live=False)
                  if lk["mine"]}
    for lu in asked:
        r = res.get(lu.unit)
        if isinstance(r, (Granted, Held)):
            since = held_since.get(lu.unit) or now if isinstance(r, Held) else now  # a renew keeps "since"
            space_vcs.lock_put(account, space, plugin, lu.unit, name=lu.name, holder_device=device_id(),
                               since=since, lease_left=r.lease_left, fence=r.fence, mine=True, reason=reason, now=now)
        elif isinstance(r, LockInfo):
            _cache_lock(ctx, r, now)
        out[lu.unit] = _lock_answer(account, space, lu.unit, r)
    return out


def renew_due(account: str, space: str, plugin: str, *, active: bool, now: float | None = None) -> list[str]:
    """Locks of mine to renew now: every 60 s while the plugin is active or the unit has
    changes not sent yet."""
    now = time.time() if now is None else now
    out = []
    for lk in space_vcs.locks(account, space, plugin, now=now):
        if not lk["mine"] or now - float(lk["renewed"]) < LOCK_RENEW_S:
            continue
        if active or _unit_unsent(account, space, plugin, lk["unit"]):
            out.append(lk["unit"])
    return out


def renew(account: str, space: str, plugin: str, backend: Any, units: list[str], *,
          now: float | None = None) -> dict[str, dict[str, Any]]:
    """Renew locks of mine: one lock call for all of them (the server takes 30 a minute),
    each keeping its reason."""
    if not units:
        return {}
    names = {lk["unit"]: lk["name"] for lk in space_vcs.locks(account, space, plugin, now=now, live=False)}
    return acquire(account, space, plugin, backend, [(unit, names.get(unit, "")) for unit in units], reason="",
                   now=now)


def _unit_unsent(account: str, space: str, plugin: str, unit: str, backend: Any = None) -> bool:
    if unit.startswith("tree:"):
        return unit in _tree_unsent(account, space, plugin, backend)
    row = _row_of_unit(unit)
    if row is None:
        return False
    r = space_vcs.row(None, account, space, plugin, *row)
    return bool(r and int(r["dirty"]))


def _tree_unsent(account: str, space: str, plugin: str, backend: Any) -> set[str]:
    """Ids of the plugin's folder units with changes here not sent yet (held back too)."""
    return {tf.tree_unit(backend, space, fid, unit) for fid, unit in ff.dirty_units(account, space, plugin)}


def _tab_closed_at(plugin: str) -> float:
    """When the person closed the plugin's last open tab here (0: one is open, or none
    closed since the app started)."""
    try:
        from backend.uefn_plugins.panel_presence import panel_state

        state = panel_state(plugin)
    except Exception:  # noqa: BLE001 — unsure: the 5-minute rule
        return 0.0
    return 0.0 if state["open"] else float(state.get("closed") or 0.0)


def idle_auto_locks(account: str, space: str, plugin: str, *, now: float | None = None,
                    backend: Any = None) -> list[str]:
    """Auto locks of mine whose unit's last change went out 30 s ago, or that were held
    when the person closed the plugin's tab (10 s ago: moving a tab between panes reopens it
    at once). A lock taken after the close (a ducky working with no tab open) keeps its
    30 s. Nothing unsent; a folder unit with changes here keeps its lock."""
    now = time.time() if now is None else now
    closed = _tab_closed_at(plugin)
    left = bool(closed) and now - closed >= TAB_CLOSED_RELEASE_S
    idle = [lk["unit"] for lk in space_vcs.locks(account, space, plugin, now=now, live=False)
            if lk["mine"] and lk["reason"] == "auto"
            and (now - float(lk["touched"]) >= AUTO_RELEASE_S or (left and float(lk["since"]) <= closed))]
    tree = _tree_unsent(account, space, plugin, backend) if any(u.startswith("tree:") for u in idle) else set()
    return [u for u in idle if u not in tree and (u.startswith("tree:") or not _unit_unsent(account, space, plugin, u))]


def release(account: str, space: str, plugin: str, backend: Any, units: list[str]) -> dict[str, Any]:
    """Release locks of mine (the cache forgets them even when the server can't be reached:
    the lease runs out there within 5 minutes)."""
    got = _call(backend.unlock, space, plugin, list(units))
    space_vcs.lock_delete(account, space, plugin, units)
    return {"ok": isinstance(got, Done), "error": "" if isinstance(got, Done) else reason_of(got)}


def release_idle(account: str, space: str, plugin: str, backend: Any, *, now: float | None = None) -> list[str]:
    units = idle_auto_locks(account, space, plugin, now=now, backend=backend)
    if units:
        release(account, space, plugin, backend, units)
    return units


def release_all(account: str, space: str, backend_for: Callable[[str], Any]) -> int:
    """Every lock of mine in the space (exit, a scope switch): best effort."""
    mine: dict[str, list[str]] = {}
    for lk in space_vcs.locks(account, space, live=False):
        if lk["mine"]:
            mine.setdefault(lk["plugin_id"], []).append(lk["unit"])
    for plugin, units in mine.items():
        backend = backend_for(plugin)
        if backend is not None:
            release(account, space, plugin, backend, units)
        else:
            space_vcs.lock_delete(account, space, plugin, units)
    return sum(len(u) for u in mine.values())


def break_lock(account: str, space: str, plugin: str, backend: Any, unit: str) -> dict[str, Any]:
    got = _call(backend.break_lock, space, plugin, unit)
    if isinstance(got, Done):
        space_vcs.lock_delete(account, space, plugin, [unit])
        return {"ok": True}
    return {"ok": False, "error": got.reason if isinstance(got, Denied) else reason_of(got)}


def ask(account: str, space: str, plugin: str, backend: Any, unit: str) -> dict[str, Any]:
    got = _call(backend.ask, space, plugin, unit)
    return {"ok": isinstance(got, Done), "error": "" if isinstance(got, Done) else reason_of(got)}


def serve_lock_requests(account: str, backend_for: Callable[[str, str], Any], *,
                        now: float | None = None) -> int:
    """Answer writers waiting for a lock (an item's first change, a ``required`` prefix).
    The plugins' bars hear it at once. Returns how many."""
    served = 0
    told: set[str] = set()
    for req in space_vcs.lock_requests_pending(account):
        backend = backend_for(req["space_id"], req["plugin_id"])
        if backend is None:
            space_vcs.lock_request_answer(account, req["space_id"], req["plugin_id"], req["unit"], "refused",
                                          "Not connected: this space can't take locks right now.")
            continue
        got = acquire(account, req["space_id"], req["plugin_id"], backend, [(req["unit"], req["name"])],
                      reason="auto", now=now)[req["unit"]]
        space_vcs.lock_request_answer(account, req["space_id"], req["plugin_id"], req["unit"],
                                      "granted" if got["ok"] else "refused", got.get("error", ""))
        served += 1
        told.add(str(req["plugin_id"]))
    _tell_bars(told)
    return served


# --------------------------------------------------------------------------- presence


def device_label() -> str:
    return (platform.node() or "This PC")[:64]


DEVICES_MAX = 200  # PC names a space remembers (the oldest go first)


_roster_lock = threading.Lock()
_roster_asked: dict[tuple[str, str], float] = {}  # (account, space) -> when the call whose roster is kept was made


def _keep_roster(account: str, space: str, asked_at: float, people: Iterable[Mapping[str, Any]],
                 usage: Mapping[str, Any] | None = None, *, sent_sha: str | None = None,
                 now: float) -> tuple[Any, dict[str, Any]] | None:
    """Keeps the roster a call made at ``asked_at`` got back (``people``; heads' ``usage``) and
    returns ``(old, new)`` for :func:`_roster_came`, or None when the answer to a later call was
    kept already: two calls at once (a live beat and the scheduler's, a beat and heads) come
    back in either order, and an older roster kept last shows teammates where they no longer
    are (Live then ends for a group that is still together). ``sent_sha``: the beat sent."""
    with _roster_lock:
        if asked_at < _roster_asked.get((account, space), 0.0):
            if sent_sha is not None:
                space_vcs.presence_put(account, space, sent_sha=sent_sha, now=now)
            return None
        _roster_asked[(account, space)] = asked_at
        old = space_vcs.presence_get(account, space)["roster"]
        roster = dict(old) if isinstance(old, dict) else {}
        roster["people"] = [dict(p) for p in people]
        if usage is not None:
            roster["usage"] = dict(usage)
        space_vcs.presence_put(account, space, with_devices(old, roster), sent_sha=sent_sha, now=now)
    return old, roster


def with_devices(old: Any, roster: dict[str, Any]) -> dict[str, Any]:
    """``roster`` with every PC name seen in the space, by device id (``devices``): the
    names ``old`` kept plus those of its people. A PC keeps its name after it leaves, so
    history can say which PC a change came from."""
    known = dict(old.get("devices") or {}) if isinstance(old, Mapping) and isinstance(old.get("devices"), Mapping) \
        else {}
    for p in roster.get("people") or []:
        if not isinstance(p, Mapping):
            continue
        device, label = str(p.get("device") or ""), str(p.get("label") or "").strip()[:64]
        if device and label:
            known.pop(device, None)
            known[device] = label  # newest last
    while len(known) > DEVICES_MAX:
        known.pop(next(iter(known)))
    return {**roster, "devices": known}


def _lock_holder(account: str, space: str, lk: Mapping[str, Any]) -> dict[str, str]:
    """A lock row's ``holder`` ('' when it is this PC's) and ``elsewhere`` (the PC's name
    when this account holds it on another PC)."""
    if lk["mine"]:
        return {"holder": "", "elsewhere": ""}
    who, pc = holder_of(account, space, lk)
    return {"holder": who, "elsewhere": pc}


def device_names(account: str, space: str) -> dict[str, str]:
    """Device id → PC name for a space: this PC's own, and every PC its rosters named."""
    roster = space_vcs.presence_get(account, space)["roster"]
    known = roster.get("devices") if isinstance(roster, dict) and isinstance(roster.get("devices"), Mapping) else {}
    return {**{str(k): str(v) for k, v in known.items()}, space_vcs.device_id(): device_label()}


def presence_blob(account: str, space: str, plugins: Iterable[str], *, now: float | None = None) -> dict[str, Any]:
    """What this PC says in the space: per plugin whether its panel is on screen, the units
    changed in the last 2 minutes (by the person or a ducky) and the locks held here."""
    from backend.uefn_plugins.panel_presence import panel_state

    acts = space_vcs.activity(account, space, now=now)
    mine = [lk for lk in space_vcs.locks(account, space, now=now) if lk["mine"]]
    names = {(lk["plugin_id"], lk["unit"]): lk["name"] for lk in mine}
    out: dict[str, Any] = {}
    for plugin in sorted(set(plugins) | {a["plugin_id"] for a in acts} | {lk["plugin_id"] for lk in mine}):
        try:
            viewing = bool(panel_state(plugin)["visible"])
        except Exception:  # noqa: BLE001 — presence never fails a beat
            viewing = False
        out[plugin] = {
            "viewing": viewing,
            "editing": [{"unitName": names.get((plugin, a["unit"])) or unit_label(account, space, plugin, a["unit"]),
                         "actor": a["actor"], "ducky": a["ducky"], "since": float(a["since"])}
                        for a in acts if a["plugin_id"] == plugin
                        and not scopes.is_local_key(plugin, _unit_name(a["unit"]))],
            "locks": sorted(lk["name"] or _unit_name(lk["unit"]) for lk in mine if lk["plugin_id"] == plugin),
        }
    team_live = None
    invites: list[dict[str, Any]] = []
    try:
        from backend.uefn_plugins import live

        team_live = live.team_room(account, space)  # the group this PC is in on the copy, every plugin with it
        invites = live.invites_of(account, space, now=now)  # teammates asked into that group
        for plugin, row in out.items():
            room = live.room_of(plugin, account, space)
            if room is not None:
                row["live"] = room  # the provider connects this PC to teammates live in the same room
            holds = live.holds_of(plugin, now=now)
            if holds:
                row["holds"] = holds  # what this PC presses: teammates grey it out
    except Exception:  # noqa: BLE001 — presence never fails a beat
        _log.debug("live rooms in presence failed", exc_info=True)
    # "label", never "device": a roster row's "device" is the device id the server adds. "ver":
    # this app's version, so a teammate's app can tell what its live speaks.
    try:
        from frontend import __version__ as ver
    except Exception:  # noqa: BLE001 — presence never fails a beat
        ver = ""
    return {"label": device_label(), "app": "open", "ver": str(ver), "plugins": out,
            **({"live": team_live} if team_live else {}), **({"invites": invites} if invites else {})}


def beat(account: str, space: str, backend: Any, plugins: Iterable[str], *, force: bool = False,
         now: float | None = None) -> dict[str, Any]:
    """Send presence when it changed, or every 60 s to keep it alive, and never more than one
    beat every 10 s (a failed one counts): the server takes 6 a minute. The roster and the
    live locks it answers with are kept. ``skipped`` when nothing was due."""
    import hashlib

    now = time.time() if now is None else now
    blob = presence_blob(account, space, plugins, now=now)
    sha = hashlib.sha256(json.dumps(blob, sort_keys=True).encode("utf-8")).hexdigest()
    last = space_vcs.presence_get(account, space)
    if not force and (now - last["sent_at"] < BEAT_MIN_S
                      or (sha == last["sent_sha"] and now - last["sent_at"] < BEAT_EVERY_S)):
        return {"ok": True, "skipped": True}
    asked_at = time.time()
    got = _call(backend.beat, space, blob)
    if not isinstance(got, Roster):
        # A failed beat counts as one: the next waits its 10 s, and goes again once due.
        space_vcs.presence_put(account, space, sent_sha=last["sent_sha"], now=now)
        return {"ok": False, "error": reason_of(got)}
    kept = _keep_roster(account, space, asked_at, got.people, sent_sha=sha, now=now)
    if kept is not None:  # an older answer's locks are as old as its roster
        _roster_came(account, space, *kept)
        _locks_from(account, space, got.locks, now=now, asked_at=asked_at)
    return {"ok": True, "people": len(got.people)}


# --------------------------------------------------------------------------- conflicts, restore, history


def resolve(account: str, space: str, plugin: str, kind: str, key: str, choice: str,
            backend: Any = None) -> dict[str, Any]:
    """Settle one conflict. ``mine``: keep this PC's version (Keep mine, Restore mine, or
    delete again); ``theirs``: keep the team's (Keep theirs, Accept delete). Either way
    the other version stops being kept here."""
    if choice not in RESOLVE_CHOICES:
        raise ValueError("Choose mine or theirs.")
    c = space_vcs.conflict_get(account, space, plugin, kind, key)
    if c is None:
        return {"ok": False, "error": "There is no conflict here any more."}
    ctx = Ctx(account, space, plugin, backend)
    reason = c["reason"]
    if reason == "lock_broken" and choice == "theirs":
        return _discard_mine(ctx, kind, key)
    target = _asset_path(ctx, key) if kind == "asset" else None
    conn = db.connect()
    a = (account, space, plugin, kind, key)
    with db.write_txn(conn):
        cur = space_vcs.row(conn, *a)
        if cur is None and choice == "mine" and _holds_mine(c):
            # The row went meanwhile: mine is made again from the conflict's copy (sent as new).
            if kind == "doc":
                space_vcs.apply_row(conn, *a, value=c["mine_value"], size=int(c["mine_size"]), sha256=c["mine_sha"],
                                    rev=0, dirty=True)
            elif c["mine_path"] and Path(c["mine_path"]).is_file():
                space_vcs.apply_row(conn, *a, value=None, size=int(c["mine_size"]), sha256=c["mine_sha"], rev=0,
                                    dirty=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(c["mine_path"], target)
        elif reason == "deleted":
            if choice == "mine" and cur is not None:
                space_vcs.set_row_state(conn, *a, "", dirty=True)  # made again on top of the delete
            elif cur is not None:
                space_vcs.delete_row(conn, *a)
                if target is not None:
                    target.unlink(missing_ok=True)
        elif reason == "mine_deleted" or (reason == "lock_broken" and int(c["mine_deleted"])):
            if choice == "mine" and cur is not None:
                space_vcs.make_tombstone(conn, *a)
                if target is not None:
                    target.unlink(missing_ok=True)
            elif cur is not None:
                space_vcs.set_row_state(conn, *a, "")
        elif choice == "mine" and cur is not None:
            if kind == "doc":
                space_vcs.apply_row(conn, *a, value=c["mine_value"], size=int(c["mine_size"]), sha256=c["mine_sha"],
                                    rev=int(cur["rev"]), dirty=True)
            elif c["mine_path"] and Path(c["mine_path"]).is_file():
                space_vcs.apply_row(conn, *a, value=None, size=int(c["mine_size"]), sha256=c["mine_sha"],
                                    rev=int(cur["rev"]), dirty=True)
                os.replace(c["mine_path"], target)
            else:
                space_vcs.set_row_state(conn, *a, "", dirty=True)
        elif cur is not None:
            space_vcs.set_row_state(conn, *a, "")
        space_vcs.conflict_delete(conn, *a)
    if c["mine_path"] and choice == "theirs":
        Path(c["mine_path"]).unlink(missing_ok=True)
    return {"ok": True}


def _discard_mine(ctx: Ctx, kind: str, key: str) -> dict[str, Any]:
    """Keep theirs after a broken lock: the unsent edit goes and the team's current version
    comes down."""
    if ctx.backend is None:
        return {"ok": False, "error": "Not connected: this space can't be reached right now."}
    got = _call(ctx.backend.fetch, ctx.space, ctx.plugin, kind, key, None)
    if not isinstance(got, IncomingItem):
        return {"ok": False, "error": reason_of(got)}
    if ctx.plugin == WORKFLOWS and kind == "doc":
        from backend.automations import owned

        owned.archive_before_adopt(ctx.scope, key)
    conn = db.connect()
    a = (ctx.account, ctx.space, ctx.plugin, kind, key)
    with db.write_txn(conn):
        space_vcs.conflict_delete(conn, *a)
        cur = space_vcs.row(conn, *a)
        if cur is not None:
            space_vcs.set_row_state(conn, *a, "", dirty=False)
    out = _apply(ctx, [IncomingChangeset(seq=0, items=(got,))], force=True)
    _deliver(ctx, out.get("changed", []))
    return {"ok": out["state"] == "ok", "error": out.get("error", "")}


def restore(account: str, space: str, plugin: str, backend: Any, *, items: Mapping[ItemId, int] | None = None,
            to_seq: int | None = None, keys: Iterable[ItemId] | None = None, message: str = "") -> dict[str, Any]:
    """Restore items to their version at a seq, or the plugin as of ``to_seq`` (``keys``:
    only those items): a new changeset on the server (never a rewrite), then pulled here.
    ``skipped``: items whose version then is no longer kept (left as they are)."""
    held_by = held_reason(account, space)
    if held_by:
        return {"ok": False, "error": held_by}
    extra = {"keys": [tuple(k) for k in keys]} if keys is not None else {}
    got = _call(backend.revert, space, plugin, items=dict(items) if items is not None else None, to_seq=to_seq,
                message=message, **extra)
    if isinstance(got, Locked):
        who = ", ".join(sorted({holder(account, space, lk.user, lk.user_name, lk.device)[0]
                                for lk in got.units})) or person()
        return {"ok": False, "error": f"Locked by {who}"}
    if not isinstance(got, Committed):
        return {"ok": False, "error": reason_of(got) or "The server didn't restore it."}
    ctx = Ctx(account, space, plugin, backend)
    with _lock(account, space, 30.0) as held:
        pulled = _pull_round(ctx, now=time.time()) if held else {"state": "busy"}
    return {"ok": True, "seq": got.seq, "skipped": [list(i) for i in got.skipped], "pulled": pulled["state"] == "ok"}


def undo(account: str, space: str, plugin: str, backend: Any, seq: int, *, message: str = "") -> dict[str, Any]:
    """Undo changeset ``seq``: each of its items goes back to what it was as of ``seq - 1``
    (the server decides: an item it made is deleted; one whose earlier version is no longer
    kept is left as it is and listed in ``skipped``, never deleted)."""
    page = _call(backend.history, space, plugin, None, seq + 1, 1)
    if not isinstance(page, History) or not page.entries or page.entries[0].seq != seq:
        return {"ok": False, "error": reason_of(page) or "That change is no longer kept."}
    keys = [tuple(vid) for vid in page.entries[0].items]
    return restore(account, space, plugin, backend, to_seq=seq - 1, keys=keys, message=message or f"Undo #{seq}")


def history(account: str, space: str, plugin: str, backend: Any, *, key: ItemId | None = None,
            before: int | None = None, limit: int = 50) -> dict[str, Any]:
    got = _call(backend.history, space, plugin, key, before, limit)
    if not isinstance(got, History):
        return {"ok": False, "error": reason_of(got)}
    return {"ok": True, "entries": [asdict(e) for e in got.entries], "more": got.more}


# --------------------------------------------------------------------------- status


def status(account: str, space: str, plugin: str, *, now: float | None = None) -> dict[str, Any]:
    """What the bar and the drawer show for one plugin's copy in a space."""
    now = time.time() if now is None else now
    head = space_vcs.head_get(account, space, plugin)
    outgoing = [{"kind": r["kind"], "key": r["key"], "size": int(r["size"]), "deleted": bool(int(r["deleted"])),
                 "state": r["sync_state"] or "queued", "reason": r["sync_error"]}
                for r in space_vcs.plugin_rows(account, space, plugin)
                if int(r["dirty"]) and not int(r["sensitive"])]
    locks = space_vcs.locks(account, space, plugin, now=now)
    roster = space_vcs.presence_get(account, space)["roster"]
    return {
        "head": int(head["seq"]),
        "remote": int(head["remote_seq"]),
        "incoming": max(0, int(head["remote_seq"]) - int(head["seq"])),
        "mode": head["mode"],
        "state": head["state"],
        "error": head["error"],
        "nextTry": float(head["next_try"]) or None,
        "syncedAt": float(head["synced_at"]) or None,
        "outgoing": outgoing,
        "sending": len(space_vcs.cs_open(account, space, plugin)),
        "conflicts": [{"kind": c["kind"], "key": c["key"], "reason": c["reason"],
                       "by": person(c["theirs_by"]) if c["theirs_by"] else "",
                       "at": c["theirs_at"], "theirsSeq": int(c["theirs_seq"])}
                      for c in space_vcs.conflicts(account, space, plugin)],
        "blocked": [{"kind": b["kind"], "key": b["key"], "error": b["error"], "attempts": int(b["attempts"]),
                     "nextTry": float(b["next_try"])} for b in space_vcs.blocked(account, space, plugin)],
        "locks": [{"unit": lk["unit"], "name": lk["name"] or _unit_name(lk["unit"]),
                   **_lock_holder(account, space, lk),
                   "mine": lk["mine"], "since": lk["since"], "leaseLeft": lk["lease_left"]} for lk in locks],
        "people": list((roster or {}).get("people") or []) if isinstance(roster, dict) else [],
    }


# --------------------------------------------------------------------------- lost access


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")[:32] or "team"


def _local_key(account: str, plugin: str, kind: str, key: str, slug: str) -> str:
    """``key`` when Local has nothing there, else ``key-from-<slug>`` (a file keeps its
    extension: ``scene-from-alpha.glb``), numbered if that is taken too."""
    def taken(k: str) -> bool:
        r = repo.get(account, scopes.PERSONAL, plugin, kind, k)
        return bool(r and not int(r["deleted"]))

    if not taken(key):
        return key
    head, sep, leaf = key.rpartition("/")
    stem, dot, ext = leaf.rpartition(".") if kind == "asset" and "." in leaf.lstrip(".") else (leaf, "", "")
    for n in range(1, 100):
        tail = f"-from-{slug}" + (f"-{n}" if n > 1 else "")
        name = f"{stem}{tail}{dot}{ext}"
        candidate = f"{head}{sep}{name}"
        limit = 128 if kind == "doc" else 256
        if len(candidate) > limit:
            candidate = f"{head}{sep}{stem[:max(1, len(stem) - (len(candidate) - limit))]}{tail}{dot}{ext}"
        if not taken(candidate):
            return candidate
    return f"{key}-from-{slug}-{uuid.uuid4().hex[:6]}"


def save_unsent_to_local(account: str, space: str, *, label: str = "") -> dict[str, Any]:
    """Access to the space is gone (or going): this PC's unsent changes are copied into
    Local, each under its own key when Local has none, else ``<key>-from-<space>``: rows
    never sent, the losing versions of mine unresolved conflicts keep, and folder units
    changed here (``<unit> (from <space>)`` when Local has that unit). The copies are
    sealed for this account already, so they are copied as they are."""
    label = label or repo.sync_get(account, space)["label"] or space
    slug = _slug(label)
    saved: list[dict[str, str]] = []
    rows = [(r["plugin_id"], r["kind"], r["key"], r["value"],
             scopes.asset_file({"account": account, "id": space}, r["plugin_id"], r["key"]), r["size"], r["sha256"])
            for r in space_vcs.unsent_rows(account, space)]
    rows += [(c["plugin_id"], c["kind"], c["key"], c["mine_value"], Path(c["mine_path"]) if c["mine_path"] else None,
              c["mine_size"], c["mine_sha"]) for c in space_vcs.unsent_conflicts(account, space)]
    for plugin, kind, key, value, src, size, sha in rows:
        if kind not in KINDS:
            continue
        new_key = _local_key(account, plugin, kind, key, slug)
        if kind == "doc":
            if value is None:
                continue
            repo.put(account, scopes.PERSONAL, plugin, "doc", new_key, value=value, size=int(size), sha256=sha,
                     dirty=False)
        else:
            if src is None or not src.is_file():
                continue
            dst = scopes.asset_file({"account": account, "id": scopes.PERSONAL}, plugin, new_key)
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = dst.with_name(f"{dst.name}.{uuid.uuid4().hex[:8]}.tmp")
            shutil.copyfile(src, temp)
            repo.put_file_row(account, scopes.PERSONAL, plugin, new_key, temp_path=temp, target_path=dst,
                              size=int(size), sha256=sha, dirty=False)
        saved.append({"plugin": plugin, "kind": kind, "key": key, "localKey": new_key})
    saved += _save_unsent_units(account, space, label)
    space_vcs.lost_choice_put(account, space, "saved")
    return {"ok": True, "saved": saved}


def _save_unsent_units(account: str, space: str, label: str) -> list[dict[str, str]]:
    """Folder units changed here and never sent (held back ones too) go into each plugin's
    Local copy (:func:`team_folders.save_unit_to_local`) before the team's copy can go."""
    saved: list[dict[str, str]] = []
    plugins = [str(r[0]) for r in db.connect().execute(
        "SELECT DISTINCT plugin_id FROM folder_files WHERE account_id=? AND space_id=? AND dirty=1 ORDER BY plugin_id",
        (account, space)).fetchall()]
    for plugin in plugins:
        for (fid, unit) in sorted(ff.dirty_units(account, space, plugin)):
            folder = tf.folder_of(plugin, fid)
            if folder is None:
                continue
            pid = unit.split("/", 1)[0] if folder.project else ""
            try:
                where = tf.save_unit_to_local(account, space, plugin, folder, unit, label, project_id=pid)
            except OSError:
                _log.warning("saving %s/%s of %s to Local failed", fid, unit, plugin, exc_info=True)
                continue
            if where:
                saved.append({"plugin": plugin, "kind": TREE, "key": _unit_key(fid, unit), "localKey": where})
    return saved


def conflicts_into_rows(account: str, space: str) -> int:
    """Upload my copy, after the space's data was reset on the server: each unresolved
    conflict's losing version of mine becomes its row again (a doc's value; a file's side
    copy back in place), so mine is what goes up. Returns how many."""
    done = 0
    files = _Files()
    conn = db.connect()
    try:
        with db.write_txn(conn):
            for c in space_vcs.unsent_conflicts(account, space):
                a = (account, space, c["plugin_id"], c["kind"], c["key"])
                if c["kind"] == "doc" and c["mine_value"] is not None:
                    space_vcs.apply_row(conn, *a, value=c["mine_value"], size=int(c["mine_size"]),
                                        sha256=c["mine_sha"], rev=0, dirty=True)
                elif c["kind"] == "asset" and c["mine_path"] and Path(c["mine_path"]).is_file():
                    target = scopes.asset_file({"account": account, "id": space}, c["plugin_id"], c["key"])
                    space_vcs.apply_row(conn, *a, value=None, size=int(c["mine_size"]), sha256=c["mine_sha"], rev=0,
                                        dirty=True)
                    files.aside(target)
                    files.move(Path(c["mine_path"]), target, keep=True)
                else:
                    continue
                space_vcs.conflict_delete(conn, *a)
                done += 1
    except BaseException:
        files.undo()
        raise
    files.committed()
    return done


def discard_unsent(account: str, space: str) -> dict[str, Any]:
    """The person chose not to keep the unsent changes of a space they lost."""
    space_vcs.lost_choice_put(account, space, "discarded")
    return {"ok": True}
