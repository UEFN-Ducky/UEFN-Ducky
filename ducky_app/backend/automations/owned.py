"""Workflows owned by Local or a shared space, database mode.

Each workflow is one doc of the host data service under the reserved id
``ducky.automations``: Local = the account's Personal scope (never leaves this PC),
a shared space = that space's scope (its changes are queued for the spaces provider
to sync; the space meta row says whether this account may change them). Docs are
sealed for the account. Unlike ``PluginData`` this module names its scope on every
call: the list shows Local and every space at once, whatever project is open.

Per-PC state never syncs: the run log, last run and "Run on this PC" live in
``workflow_runtime``; saved versions in ``workflow_versions`` (sealed too).

Folders: each workflow carries its own ``folder`` path. The owner's folder list
(empty folders included) is one more doc of the same scope under the reserved key
``_folders``, ``{"folders": ["Play tests", "Play tests/Tycoon"]}``, so a space's
tree syncs with its workflows and the website can read it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
import uuid
from typing import Any, Iterator

from backend.store.repos import automations as legacy
from backend.store.repos import plugin_data as data
from backend.store.repos import workflows as runtime
from backend.uefn_plugins import scopes

_log = logging.getLogger(__name__)

DOC_PLUGIN = "ducky.automations"
LOCAL = "local"
RUN_CAP = 20
BEFORE_SYNC_NOTE = "Your copy before sync"
NO_PERMISSION = "This account can't change these workflows."
_DOC_FIELDS = ("id", "name", "description", "enabled", "folder", "graph", "updated", "created_by")
FOLDERS_KEY = "_folders"  # never a workflow id (save refuses it)
FOLDERS_MAX = 2000
_UNLISTED = ("unavailable", "lost")  # space states whose workflows are locked on this PC

_CLAIMED: set[tuple[str, str]] = set()  # (database file, account) checked this process
_SPLIT: set[tuple[str, str]] = set()  # (database file, account) whose forked ids were split this process
_CLAIM_LOCK = threading.Lock()
_DUPLICATES: set[tuple[str, ...]] = set()  # (workflow id, owner ids…) already logged
AMBIGUOUS = ("More than one copy on this PC has this workflow's id, so it isn't clear which one you mean. "
             "Restart UEFN Ducky and try again.")


class ReadOnlyOwner(PermissionError):
    """The workflow's owner can't be changed from this PC right now (the message says why)."""


# --------------------------------------------------------------------------- owners


def account() -> str:
    aid = scopes.account_id()
    _claim_legacy(aid)
    _split_forks(aid)
    return aid


def _refusal(scope: dict[str, Any]) -> str:
    """Why this account can't change the scope's workflows ("" when it can)."""
    if scope["kind"] != "shared" or runtime.workflows_writable(scope["account"], scope["id"]):
        return ""
    from backend.uefn_plugins.spaces import provider

    return provider().workflow_edit_refusal(scope["account"], scope["id"], scope["label"]) or NO_PERMISSION


def _space_scopes(aid: str) -> list[dict[str, Any]]:
    """Every shared space this account keeps a copy of, whatever its state."""
    if aid == scopes.LOCAL:
        return []
    return [scopes.shared_scope(aid, space) for space in runtime.synced_spaces(aid) if scopes.valid_space_id(space)]


def owner_scopes(aid: str | None = None) -> list[dict[str, Any]]:
    """Local first, then every shared space this account keeps a copy of. A space whose
    access was lost, or that isn't available now, is left out: its workflows stay locked
    on this PC (not listed, never run) until it is back or the copy goes."""
    aid = aid or account()
    return [scopes.personal_scope(aid)] + [s for s in _space_scopes(aid) if s["state"] not in _UNLISTED]


def unlisted_reason(scope: dict[str, Any]) -> str:
    """Why a space :func:`owner_scopes` leaves out takes no changes from this PC."""
    if scope["state"] == "lost":
        return f"You no longer have access to {scope['label']}. Its workflows on this PC are locked."
    why = f" ({scope['reason']})" if scope.get("reason") else ""
    return f"{scope['label']} isn't available right now{why}. Its workflows can't change until it's back."


def held_unlisted(workflow_id: str, aid: str | None = None) -> dict[str, Any] | None:
    """The left-out space (access lost, not available now) that still holds this workflow
    on this PC: a save must refuse instead of forking it into Local under the same id."""
    aid = aid or account()
    for scope in _space_scopes(aid):
        if scope["state"] in _UNLISTED:
            row = data.get(aid, scope["id"], DOC_PLUGIN, "doc", workflow_id)
            if row and not row["deleted"]:
                return scope
    return None


def _reason(scope: dict[str, Any]) -> str:
    state = scope["state"]
    if state == "locked":
        return "Waiting for your account's data key. Read-only until you're back online."
    if state == "waiting":
        return f"Waiting for {scope['label']}'s data. Read-only until it arrives."
    if state == "reset":
        return (f"{scope['label']}'s data was reset on the server. Read-only until you choose what to do with "
                "this PC's copy.")
    # Paused still lists the space and still takes workflows; sharing waits until it resumes.
    return _refusal(scope)


def owner_view(scope: dict[str, Any]) -> dict[str, Any]:
    shared = scope["kind"] == "shared"
    reason = _reason(scope)
    view = {
        "id": scope["id"] if shared else LOCAL,
        "kind": "shared" if shared else LOCAL,
        "label": scope["label"] if shared else "Local",
        "state": scope["state"],
        "readOnly": bool(reason),
        "reason": reason,
    }
    if shared:
        from backend.uefn_plugins.spaces import provider

        view["kindLabel"] = provider().kind_label
        view["pausedLabel"] = provider().paused_label
    return view


def scope_for(owner: str, aid: str | None = None) -> dict[str, Any]:
    aid = aid or account()
    key = (owner or LOCAL).strip()
    for scope in owner_scopes(aid):
        if (scope["kind"] == "personal" and key in (LOCAL, scopes.PERSONAL)) or scope["id"] == key:
            return scope
    for scope in _space_scopes(aid):
        if scope["id"] == key:
            raise ReadOnlyOwner(unlisted_reason(scope))
    raise ValueError(f"unknown workflow owner: {key}")


def writable(scope: dict[str, Any]) -> dict[str, Any]:
    reason = _reason(scope)
    if reason:
        raise ReadOnlyOwner(reason)
    return scope


# --------------------------------------------------------------------------- docs


def _open(stored: str, aid: str) -> Any:
    from backend.uefn_plugins.data_crypto import open_text

    return json.loads(open_text(stored, aid))


def _seal(value: Any, aid: str) -> str:
    from backend.uefn_plugins.data_crypto import seal_text

    return seal_text(json.dumps(value, ensure_ascii=False), aid)


def _doc_of(row: dict[str, Any] | None, aid: str) -> dict[str, Any] | None:
    from backend.uefn_plugins.data_crypto import Locked

    if not row or row["deleted"] or row["value"] is None:
        return None
    try:
        doc = _open(row["value"], aid)
    except (Locked, OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    doc["id"] = row["key"]
    return doc


def docs(scope: dict[str, Any]) -> list[dict[str, Any]]:
    aid = scope["account"]
    out = []
    for row in data.rows(aid, scope["id"], DOC_PLUGIN, "doc"):
        if row["key"] == FOLDERS_KEY:
            continue
        doc = _doc_of(row, aid)
        if doc is not None:
            out.append(doc)
    return out


def _held(workflow_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    wid = (workflow_id or "").strip()
    if not scopes.valid_doc_key(wid) or wid == FOLDERS_KEY:
        return []
    held = []
    for scope in owner_scopes():
        row = data.get(scope["account"], scope["id"], DOC_PLUGIN, "doc", wid)
        if row and not row["deleted"] and row["value"] is not None:
            held.append((scope, row))
    return held


def holders(workflow_id: str) -> list[dict[str, Any]]:
    """The owners holding a workflow under this id: more than one only for a duplicate
    id (:func:`find`), which an action by id alone must not guess at."""
    return [scope for scope, _ in _held(workflow_id)]


def find(workflow_id: str, owner: str = "") -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(scope, doc)`` of a workflow this account can see; ``owner``'s copy (an owner id,
    ``local``) when it holds one. Ids are unique per account (:func:`_split_forks`); one
    held by more than one owner all the same (pulled since) resolves to the space's copy,
    so this PC keeps following the team's, and the duplicate is logged."""
    held = _held(workflow_id)
    if len(held) > 1:
        _note_duplicate(workflow_id.strip(), [scope for scope, _ in held])
    held.sort(key=lambda hit: hit[0]["kind"] != "shared")  # stable: the first space's copy, else Local
    if owner:
        held.sort(key=lambda hit: not _is_owner(hit[0], owner))
    for scope, row in held:
        doc = _doc_of(row, scope["account"])
        if doc is not None:
            return scope, doc
    return None


def _is_owner(scope: dict[str, Any], owner: str) -> bool:
    key = owner.strip()
    return scope["id"] == key or (scope["kind"] == "personal" and key in (LOCAL, scopes.PERSONAL))


def _note_duplicate(workflow_id: str, owners: list[dict[str, Any]]) -> None:
    mark = (workflow_id, *(scope["id"] for scope in owners))
    if mark in _DUPLICATES:
        return
    _DUPLICATES.add(mark)
    _log.warning(
        "workflow %s is in more than one owner (%s); the shared copy is the one used", workflow_id,
        ", ".join(scope["label"] for scope in owners),
    )


def _body(doc: dict[str, Any]) -> dict[str, Any]:
    """The synced part of a workflow (created_by only when set: older workflows keep their
    exact stored form)."""
    return {key: doc.get(key) for key in _DOC_FIELDS if key != "created_by" or doc.get(key)}


def _max_bytes(scope: dict[str, Any]) -> int:
    """A doc's size limit in ``scope``: a shared copy's is what still fits once sealed."""
    return scopes.DOC_MAX_BYTES - (scopes.SHARED_SEAL_OVERHEAD if scope["kind"] == "shared" else 0)


@contextlib.contextmanager
def _team_write(scope: dict[str, Any], key: str, message: str, *, changes: bool = True) -> Iterator[str | None]:
    """A workflow write to a shared copy goes through the writer's checks like any plugin
    data: refused while someone else holds the key's lock (``scopes.LockedOut``, a
    ``ReadOnlyScope``), one write group (one changeset, unless a call already opened one),
    and presence says who edits it. Yields the group id to store with the row ('' for none),
    or None for a Local copy, a copy on the older sync, or nothing to write."""
    if scope["kind"] != "shared" or not changes:
        yield None
        return
    actor, ducky = scopes.caller()
    with scopes.write_group(DOC_PLUGIN, actor=actor, ducky=ducky, message=message):
        group = scopes._team_write(scope, DOC_PLUGIN, "doc", key)
        yield group
        if group is not None:
            scopes._note_write(scope, DOC_PLUGIN, "doc", key)


def write(scope: dict[str, Any], doc: dict[str, Any], *, versions: tuple[dict[str, Any], ...] = ()) -> None:
    """Store the synced part of ``doc`` in ``scope`` (a shared copy is queued to push),
    and the saved copies in ``versions``, in one transaction: a save that fails
    leaves no version behind. Everything is sealed before the transaction opens."""
    from backend.store import db
    from backend.uefn_plugins.data_crypto import seal_text

    raw = scopes.encode_doc(_body(doc))
    if len(raw) > _max_bytes(scope):
        size = "too big to share" if scope["kind"] == "shared" else "over 1 MB"
        raise ValueError(f"This workflow is {size}. Split it into smaller workflows.")
    aid, wid = scope["account"], str(doc["id"])
    sha = hashlib.sha256(raw).hexdigest()
    old = data.get(aid, scope["id"], DOC_PLUGIN, "doc", wid)
    unchanged = bool(old and not old["deleted"] and old["sha256"] == sha)
    sealed = None if unchanged else seal_text(raw.decode("utf-8"), aid)
    snaps = [(float(v.get("updated") or 0.0), _seal(_snapshot(v), aid)) for v in versions]
    with _team_write(scope, wid, f"Workflow {doc.get('name') or wid}", changes=sealed is not None) as group:
        conn = db.connect()
        with db.write_txn(conn):
            for saved_at, text in snaps:
                legacy.archive(conn, wid, saved_at, text)
            if sealed is not None:
                data.upsert(conn, aid, scope["id"], DOC_PLUGIN, "doc", wid, value=sealed, size=len(raw), sha256=sha,
                            dirty=scope["kind"] == "shared", group_id=group or "")


def folders(scope: dict[str, Any]) -> list[str]:
    """The owner's folder list as stored (paths, empty folders included)."""
    aid = scope["account"]
    doc = _doc_of(data.get(aid, scope["id"], DOC_PLUGIN, "doc", FOLDERS_KEY), aid) or {}
    raw = doc.get("folders")
    return [str(path) for path in raw if isinstance(path, str)] if isinstance(raw, list) else []


def write_folders(scope: dict[str, Any], paths: list[str]) -> None:
    """Replace the owner's folder list (a space's is queued to push like a workflow)."""
    from backend.store import db
    from backend.uefn_plugins.data_crypto import seal_text

    raw = scopes.encode_doc({"folders": list(paths)[:FOLDERS_MAX]})
    if len(raw) > _max_bytes(scope):
        size = "too big to share" if scope["kind"] == "shared" else "over 1 MB"
        raise ValueError(f"The folder list is {size}. Use fewer or shorter folder names.")
    aid = scope["account"]
    sha = hashlib.sha256(raw).hexdigest()
    old = data.get(aid, scope["id"], DOC_PLUGIN, "doc", FOLDERS_KEY)
    if old and not old["deleted"] and old["sha256"] == sha:
        return
    sealed = seal_text(raw.decode("utf-8"), aid)
    with _team_write(scope, FOLDERS_KEY, "Workflow folders") as group:
        conn = db.connect()
        with db.write_txn(conn):
            data.upsert(conn, aid, scope["id"], DOC_PLUGIN, "doc", FOLDERS_KEY, value=sealed, size=len(raw),
                        sha256=sha, dirty=scope["kind"] == "shared", group_id=group or "")


def remove(scope: dict[str, Any], workflow_id: str) -> bool:
    aid = scope["account"]
    old = data.get(aid, scope["id"], DOC_PLUGIN, "doc", workflow_id)
    if not old or old["deleted"]:
        return False
    # Always a tombstone in a shared copy: the server may hold it even at rev 0 (a push in
    # flight, a lost answer), and a delete of an item it never had is harmless.
    with _team_write(scope, workflow_id, f"Deleted workflow {workflow_id}") as group:
        data.remove(aid, scope["id"], DOC_PLUGIN, "doc", workflow_id, tombstone=scope["kind"] == "shared",
                    group_id=group or "")
    return True


# --------------------------------------------------------------------------- per-PC state


def runs(aid: str, workflow_id: str) -> list[dict[str, Any]]:
    from backend.uefn_plugins.data_crypto import Locked

    stored = runtime.runtime_get(aid, workflow_id)["runs"]
    if not stored:
        return []
    try:
        out = _open(stored, aid)
    except (Locked, OSError, ValueError):
        return []
    return out if isinstance(out, list) else []


def state(aid: str, workflow_id: str) -> dict[str, Any]:
    row = runtime.runtime_get(aid, workflow_id)
    return {"last_run": row["last_run"], "run_here": row["run_here"]}


def _for_log(run: Any) -> Any:
    """A run as the log keeps it. A step keeps what the log shows and what a later run reads back
    (label, state, error, inputs, its code log); the values it made stay once, in the run's
    ``node_outputs`` (long text cut), and its raw result only when it failed. Kept whole, one tool
    answer was in a run five times: 20 runs of one workflow were 6 MB, sealed again on every run
    and sent on every open, and nothing read those copies (Nov 2026)."""

    def steps(rows: Any) -> list[Any]:
        out = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                row = {k: v for k, v in row.items() if k != "outputs" and (k != "result" or row.get("ok") is False)}
                if "substeps" in row:
                    row["substeps"] = steps(row["substeps"])
            out.append(row)
        return out

    return {**run, "steps": steps(run["steps"])} if isinstance(run, dict) and isinstance(run.get("steps"), list) else run


def append_run(aid: str, workflow_id: str, run: dict[str, Any], at: float, *, keep: int = RUN_CAP) -> list[dict[str, Any]]:
    """Add a run, keep the last ``keep``; returns the runs that were dropped."""
    every = [_for_log(one) for one in runs(aid, workflow_id) + [run]]  # (runs stored whole before slim down here too)
    cut = max(len(every) - max(int(keep), 1), 0)
    runtime.runtime_put(aid, workflow_id, runs=_seal(every[cut:], aid), last_run=at)
    return every[:cut]


def clear_runs(aid: str, workflow_id: str) -> None:
    runtime.runtime_put(aid, workflow_id, runs="")


def set_run_here(aid: str, workflow_id: str, on: bool) -> None:
    runtime.runtime_put(aid, workflow_id, run_here=bool(on))


def forget(aid: str, workflow_ids: list[str]) -> None:
    runtime.runtime_delete(aid, workflow_ids)


# --------------------------------------------------------------------------- saved versions


def _snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    from backend.automations.versions import snapshot

    snap = snapshot(doc)
    if doc.get("note"):
        snap["note"] = doc["note"]
    return snap


def open_version(stored: str, aid: str) -> dict[str, Any] | None:
    """A saved copy this account may read: sealed for it, or legacy plaintext."""
    from backend.uefn_plugins.data_crypto import Locked, is_sealed

    try:
        doc = _open(stored, aid) if is_sealed(stored) else json.loads(stored)
    except (Locked, OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def archive_before_adopt(scope: dict[str, Any], workflow_id: str) -> None:
    """A sync is about to replace this PC's unpushed edit with the other copy: keep the
    local one as a saved version so History can bring it back. Inside a transaction the
    caller holds (a pull applying a changeset), it lands in that one."""
    from backend.store import db

    if workflow_id == FOLDERS_KEY:
        return  # the folder list has no History: the server's list wins
    aid = scope["account"]
    doc = _doc_of(data.get(aid, scope["id"], DOC_PLUGIN, "doc", workflow_id), aid)
    if doc is None:
        return
    text = _seal({**_snapshot(doc), "note": BEFORE_SYNC_NOTE}, aid)
    conn = db.connect()
    with contextlib.nullcontext(conn) if conn.in_transaction else db.write_txn(conn):
        legacy.archive(conn, workflow_id, float(doc.get("updated") or 0.0), text)


# --------------------------------------------------------------------------- claims and imports


def _move_versions(old_id: str, new_id: str, src: str | None, aid: str) -> None:
    """Re-seal a workflow's saved copies for ``aid``. ``src`` None = legacy plaintext."""
    from backend.uefn_plugins.data_crypto import is_sealed

    def convert(stored: str) -> str | None:
        if src is None and is_sealed(stored):
            return None
        doc = open_version(stored, src or aid)
        return _seal(doc, aid) if doc is not None else None

    legacy.reseal_versions(old_id, new_id, convert)


def _claim_legacy(aid: str) -> None:
    """Rows of the old shared ``automations`` table join the first account that opens
    workflows (``_local`` when signed out), sealed for it, like plugin_kv (0009)."""
    from backend.store import db
    from backend.uefn_plugins.data_crypto import available

    key = (str(db.db_path()), aid)
    if key in _CLAIMED:
        return
    with _CLAIM_LOCK:
        if key in _CLAIMED:
            return
        if not legacy.has_legacy():
            _CLAIMED.add(key)
            return
        if not available(aid):
            return  # locked: nothing can be sealed yet, try again on the next call
        personal = scopes.personal_scope(aid)
        for row in legacy.list_all():
            old_id = row["id"]
            wid = old_id if scopes.valid_doc_key(old_id) else str(uuid.uuid4())
            write(personal, {**row, "id": wid})
            kept = list(row.get("runs") or [])[-RUN_CAP:]
            runtime.runtime_put(aid, wid, runs=_seal(kept, aid) if kept else "",
                                last_run=float(row.get("last_run") or 0.0))
            _move_versions(old_id, wid, None, aid)
            legacy.delete(old_id)
        _CLAIMED.add(key)


def _in_a_space(aid: str, spaces: list[str], workflow_id: str) -> bool:
    return any((row := data.get(aid, space, DOC_PLUGIN, "doc", workflow_id)) and not row["deleted"]
               for space in spaces)


def _split_forks(aid: str) -> None:
    """Older builds saved a space's workflow into Local under the space's id while that
    space wasn't listed here, so one id named two workflows and an action by id (Delete,
    a move) could hit the team's copy instead of this PC's. Each such Local copy gets a
    fresh id; its runs, saved versions and code approvals come along. Once per process
    per account, before anything lists workflows: saves refuse to fork since."""
    from backend.store import db
    from backend.uefn_plugins.data_crypto import available

    key = (str(db.db_path()), aid)
    if key in _SPLIT:
        return
    with _CLAIM_LOCK:
        if key in _SPLIT:
            return
        if aid != scopes.LOCAL:
            if not available(aid):
                return  # locked: nothing can be sealed yet, try again on the next call
            spaces = [space for space in runtime.synced_spaces(aid) if scopes.valid_space_id(space)]
            for row in data.rows(aid, scopes.PERSONAL, DOC_PLUGIN, "doc") if spaces else []:
                if row["key"] != FOLDERS_KEY and _in_a_space(aid, spaces, row["key"]):
                    _split(aid, spaces, row["key"])
        _SPLIT.add(key)


def _split(aid: str, spaces: list[str], workflow_id: str) -> None:
    from backend.automations.store import carry_approvals
    from backend.store import db
    from backend.uefn_plugins.data_crypto import seal_text

    new_id = str(uuid.uuid4())
    conn = db.connect()
    with db.write_txn(conn):
        # Read again inside the transaction: another process may have split it already.
        doc = _doc_of(data.get(aid, scopes.PERSONAL, DOC_PLUGIN, "doc", workflow_id), aid)
        if doc is None or not _in_a_space(aid, spaces, workflow_id):
            return
        raw = scopes.encode_doc(_body({**doc, "id": new_id}))
        data.upsert(conn, aid, scopes.PERSONAL, DOC_PLUGIN, "doc", new_id, value=seal_text(raw.decode("utf-8"), aid),
                    size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), dirty=False)
        data.delete_row(conn, aid, scopes.PERSONAL, DOC_PLUGIN, "doc", workflow_id)
        legacy.copy_versions(conn, workflow_id, new_id)
    old = runtime.runtime_get(aid, workflow_id)  # the run log is per id here: both copies' runs
    if old["runs"] or old["last_run"]:
        runtime.runtime_put(aid, new_id, runs=old["runs"], last_run=old["last_run"])
    carry_approvals({"id": new_id, "graph": doc.get("graph"), "owner": {"kind": LOCAL}}, [workflow_id])
    _log.info("workflow %s: this PC's Local copy of a space's workflow now has its own id, %s", workflow_id, new_id)


def local_import_count(aid: str) -> int:
    """Workflows made while signed out on this PC, offered to the signed-in account."""
    if aid == scopes.LOCAL:
        return 0
    return len([r for r in data.rows(scopes.LOCAL, scopes.PERSONAL, DOC_PLUGIN, "doc") if r["key"] != FOLDERS_KEY])


def import_local(aid: str) -> int:
    """Move the signed-out (``_local``) workflows into this account's Local folder."""
    if aid == scopes.LOCAL:
        return 0
    signed_out = scopes.personal_scope(scopes.LOCAL)
    mine = writable(scopes.personal_scope(aid))
    moved = 0
    for doc in docs(signed_out):
        wid = str(doc["id"])
        write(mine, doc)
        old = runtime.runtime_get(scopes.LOCAL, wid)
        kept = runs(scopes.LOCAL, wid)
        runtime.runtime_put(aid, wid, runs=_seal(kept, aid) if kept else "", last_run=old["last_run"],
                            run_here=old["run_here"])
        _move_versions(wid, wid, scopes.LOCAL, aid)
        remove(signed_out, wid)
        forget(scopes.LOCAL, [wid])
        moved += 1
    left = folders(signed_out)
    if left:
        write_folders(mine, list(dict.fromkeys(folders(mine) + left)))
        remove(signed_out, FOLDERS_KEY)
    return moved


def reset_for_tests() -> None:
    _CLAIMED.clear()
    _SPLIT.clear()
