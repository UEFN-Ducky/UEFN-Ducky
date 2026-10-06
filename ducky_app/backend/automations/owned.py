"""Workflows owned by Local or a team (DuckyOS plan §14), database mode.

Each workflow is one doc of the host data service under the reserved id
``ducky.automations``: Local = the account's Personal scope (never leaves this PC),
Team = that team's scope (synced by :mod:`backend.uefn_plugins.team_sync`; the
Store refuses pushes from members without Manage automations). Docs are sealed
for the account (plan §13). Unlike ``PluginData`` this module names its scope on
every call: the list shows Local and every team at once, whatever project is open.

Per-PC state never syncs: the run log, last run and "Run on this PC" live in
``workflow_runtime``; saved versions in ``workflow_versions`` (sealed too).

Folders: each workflow carries its own ``folder`` path. The owner's folder list
(empty folders included) is one more doc of the same scope under the reserved key
``_folders``, ``{"folders": ["Play tests", "Play tests/Tycoon"]}``, so a team's
tree syncs with its workflows and the website can read it.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any

from backend.store.repos import automations as legacy
from backend.store.repos import plugin_data as data
from backend.store.repos import workflows as runtime
from backend.uefn_plugins import scopes

DOC_PLUGIN = "ducky.automations"
LOCAL = "local"
RUN_CAP = 20
BEFORE_SYNC_NOTE = "Your copy before sync"
NO_PERMISSION = "Only members with Manage automations can change team workflows."
_DOC_FIELDS = ("id", "name", "description", "enabled", "folder", "graph", "updated")
FOLDERS_KEY = "_folders"  # never a workflow id (save refuses it)
FOLDERS_MAX = 2000

_CLAIMED: set[tuple[str, str]] = set()  # (database file, account) checked this process
_CLAIM_LOCK = threading.Lock()


class ReadOnlyOwner(PermissionError):
    """The workflow's owner can't be changed from this PC right now (the message says why)."""


# --------------------------------------------------------------------------- owners


def account() -> str:
    aid = scopes.account_id()
    _claim_legacy(aid)
    return aid


def _can_automate(scope: dict[str, Any]) -> bool:
    return scope["kind"] != "team" or runtime.perms_get(scope["account"], scope["id"])


def owner_scopes(aid: str | None = None) -> list[dict[str, Any]]:
    """Local first, then every team this account belongs to."""
    aid = aid or account()
    out = [scopes.personal_scope(aid)]
    if aid == scopes.LOCAL:
        return out
    for team in runtime.synced_teams(aid):
        if scopes.valid_team_id(team):
            scope = scopes.team_scope(aid, team)
            if scope["state"] != "unavailable":
                out.append(scope)
    return out


def _reason(scope: dict[str, Any]) -> str:
    state = scope["state"]
    if state == "locked":
        return "Waiting for your account's data key. Read-only until you're back online."
    if state == "waiting":
        return f"Waiting for team data from {scope['label']}. Read-only until it arrives."
    # Paused (Team Private off) still lists the team and still takes workflows.
    # Sharing waits until the plan is active again.
    if not _can_automate(scope):
        return NO_PERMISSION
    return ""


def owner_view(scope: dict[str, Any]) -> dict[str, Any]:
    team = scope["kind"] == "team"
    reason = _reason(scope)
    return {
        "id": scope["id"] if team else LOCAL,
        "kind": "team" if team else LOCAL,
        "label": scope["label"] if team else "Local",
        "state": scope["state"],
        "readOnly": bool(reason),
        "reason": reason,
    }


def scope_for(owner: str, aid: str | None = None) -> dict[str, Any]:
    key = (owner or LOCAL).strip()
    for scope in owner_scopes(aid):
        if (scope["kind"] == "personal" and key in (LOCAL, scopes.PERSONAL)) or scope["id"] == key:
            return scope
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


def find(workflow_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(scope, doc)`` of a workflow this account can see."""
    wid = (workflow_id or "").strip()
    if not scopes.valid_doc_key(wid) or wid == FOLDERS_KEY:
        return None
    for scope in owner_scopes():
        doc = _doc_of(data.get(scope["account"], scope["id"], DOC_PLUGIN, "doc", wid), scope["account"])
        if doc is not None:
            return scope, doc
    return None


def write(scope: dict[str, Any], doc: dict[str, Any], *, versions: tuple[dict[str, Any], ...] = ()) -> None:
    """Store the synced part of ``doc`` in ``scope`` (a team copy is queued to push),
    and the saved copies in ``versions``, in one transaction: a save that fails
    leaves no version behind. Everything is sealed before the transaction opens."""
    from backend.store import db
    from backend.uefn_plugins.data_crypto import seal_text

    body = {key: doc.get(key) for key in _DOC_FIELDS}
    raw = scopes.encode_doc(body)
    if len(raw) > scopes.DOC_MAX_BYTES:
        raise ValueError("This workflow is over 1 MB. Split it into smaller workflows.")
    aid, wid = scope["account"], str(doc["id"])
    sha = hashlib.sha256(raw).hexdigest()
    old = data.get(aid, scope["id"], DOC_PLUGIN, "doc", wid)
    unchanged = bool(old and not old["deleted"] and old["sha256"] == sha)
    sealed = None if unchanged else seal_text(raw.decode("utf-8"), aid)
    snaps = [(float(v.get("updated") or 0.0), _seal(_snapshot(v), aid)) for v in versions]
    conn = db.connect()
    with db.write_txn(conn):
        for saved_at, text in snaps:
            legacy.archive(conn, wid, saved_at, text)
        if sealed is not None:
            data.upsert(conn, aid, scope["id"], DOC_PLUGIN, "doc", wid, value=sealed, size=len(raw), sha256=sha,
                        dirty=scope["kind"] == "team")


def folders(scope: dict[str, Any]) -> list[str]:
    """The owner's folder list as stored (paths, empty folders included)."""
    aid = scope["account"]
    doc = _doc_of(data.get(aid, scope["id"], DOC_PLUGIN, "doc", FOLDERS_KEY), aid) or {}
    raw = doc.get("folders")
    return [str(path) for path in raw if isinstance(path, str)] if isinstance(raw, list) else []


def write_folders(scope: dict[str, Any], paths: list[str]) -> None:
    """Replace the owner's folder list (a team's is queued to push like a workflow)."""
    from backend.store import db
    from backend.uefn_plugins.data_crypto import seal_text

    raw = scopes.encode_doc({"folders": list(paths)[:FOLDERS_MAX]})
    aid = scope["account"]
    sha = hashlib.sha256(raw).hexdigest()
    old = data.get(aid, scope["id"], DOC_PLUGIN, "doc", FOLDERS_KEY)
    if old and not old["deleted"] and old["sha256"] == sha:
        return
    sealed = seal_text(raw.decode("utf-8"), aid)
    conn = db.connect()
    with db.write_txn(conn):
        data.upsert(conn, aid, scope["id"], DOC_PLUGIN, "doc", FOLDERS_KEY, value=sealed, size=len(raw), sha256=sha,
                    dirty=scope["kind"] == "team")


def remove(scope: dict[str, Any], workflow_id: str) -> bool:
    aid = scope["account"]
    old = data.get(aid, scope["id"], DOC_PLUGIN, "doc", workflow_id)
    if not old or old["deleted"]:
        return False
    data.remove(aid, scope["id"], DOC_PLUGIN, "doc", workflow_id,
                tombstone=scope["kind"] == "team" and old["rev"] > 0)
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


def append_run(aid: str, workflow_id: str, run: dict[str, Any], at: float) -> None:
    kept = (runs(aid, workflow_id) + [run])[-RUN_CAP:]
    runtime.runtime_put(aid, workflow_id, runs=_seal(kept, aid), last_run=at)


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
    """Team sync is about to replace this PC's unpushed edit with the server copy:
    keep the local one as a saved version so History can bring it back."""
    from backend.store import db

    if workflow_id == FOLDERS_KEY:
        return  # the folder list has no History: the server's list wins
    aid = scope["account"]
    doc = _doc_of(data.get(aid, scope["id"], DOC_PLUGIN, "doc", workflow_id), aid)
    if doc is None:
        return
    text = _seal({**_snapshot(doc), "note": BEFORE_SYNC_NOTE}, aid)
    conn = db.connect()
    with db.write_txn(conn):
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
