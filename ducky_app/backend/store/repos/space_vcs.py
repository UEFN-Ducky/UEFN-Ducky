"""A shared space's copy as a versioned repo (migration 0021): heads, changesets waiting
to go out, conflicts, incoming items set aside, the lock and presence caches, recent
activity, write groups, lock requests, merge bases and per-unit applied seqs.

``plugin_data`` stays the working copy; the row helpers here are the ones the version
engine (:mod:`backend.uefn_plugins.vcs_engine`) needs on top of
:mod:`backend.store.repos.plugin_data`. Functions taking ``conn`` run inside a
transaction the caller holds; the others open their own. Every call names its
``(account, space)``.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterable

from backend.store import db

_ROW_COLS = ("plugin_id, kind, key, value, size, sha256, rev, dirty, deleted, sensitive, updated, sync_state, "
             "sync_error, attempts, next_try, group_id")
_ROW_WHERE = "account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=?"
# Rows a push leaves out: refused and blocked wait for a retry, conflict and locked_out
# for the person or the lock's release.
HELD_STATES = ("refused", "blocked", "conflict", "locked_out")
BACKOFF_S = (30.0, 120.0, 300.0, 900.0)  # failed rounds in a row: 1, 2, 3, 4 or more
BLOCKED_MAX_WAIT_S = 3600.0
ACTIVITY_KEEP_S = 120.0
CHANGESET_KEEP_S = 24 * 3600.0
GROUP_KEEP_S = 24 * 3600.0
REQUEST_KEEP_S = 60.0
LOST_CHOICES = frozenset({"saved", "discarded"})
DEVICE_KEY = "space_device_id"
# Per-plugin epoch row (workspace_state): what pulls changed, for api.data.on_change in
# every process that loaded the plugin.
CHANGE_LOG_PREFIX = "plugin_epoch:"
CHANGE_LOG_KEEP = 32
CHANGE_KEYS_MAX = 500
# Changesets of a plugin waiting behind "Update now" (workspace_state, every process).
INCOMING_PREFIX = "space_incoming:"
# Folder units whose changeset failed as a whole: their tries and when the next one goes.
UNIT_TRIES_PREFIX = "space_unit_tries:"
# A switch getting a team's folder copy first: its progress for the bar.
BINDING_PREFIX = "plugin_binding:"
BINDING_KEEP_S = 120.0  # a progress row older than this was left by a switch that died


def _row(r: Any) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def _now(at: float | None) -> float:
    return time.time() if at is None else float(at)


# --------------------------------------------------------------------------- working-copy rows


def row(conn: Any, account: str, space: str, plugin: str, kind: str, key: str) -> dict[str, Any] | None:
    """One ``plugin_data`` row with its write group."""
    r = (conn or db.connect()).execute(
        f"SELECT {_ROW_COLS} FROM plugin_data WHERE {_ROW_WHERE}", (account, space, plugin, kind, key)
    ).fetchone()
    return _row(r) if r else None


def plugin_rows(account: str, space: str, plugin: str) -> list[dict[str, Any]]:
    """Every row of one plugin in the space, tombstones and sensitive ones too."""
    out = db.connect().execute(
        f"SELECT {_ROW_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=? ORDER BY kind, key",
        (account, space, plugin),
    ).fetchall()
    return [_row(r) for r in out]


def has_outgoing(account: str, space: str, plugin: str, now: float | None = None) -> bool:
    """Whether :func:`outgoing_rows` has any (without reading their content)."""
    held = ", ".join("?" * len(HELD_STATES))
    return db.connect().execute(
        f"SELECT 1 FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=? AND dirty=1 AND sensitive=0 "
        f"AND sync_state NOT IN ({held}) AND next_try<=? LIMIT 1",
        (account, space, plugin, *HELD_STATES, _now(now)),
    ).fetchone() is not None


def outgoing_rows(account: str, space: str, plugin: str, now: float | None = None) -> list[dict[str, Any]]:
    """Changes ready to go out: dirty, never sensitive, not held (:data:`HELD_STATES`) and
    not backing off. Oldest first."""
    held = ", ".join("?" * len(HELD_STATES))
    out = db.connect().execute(
        f"SELECT {_ROW_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=? AND dirty=1 "
        f"AND sensitive=0 AND sync_state NOT IN ({held}) AND next_try<=? ORDER BY updated, kind, key",
        (account, space, plugin, *HELD_STATES, _now(now)),
    ).fetchall()
    return [_row(r) for r in out]


def apply_row(
    conn: Any,
    account: str,
    space: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    value: str | None,
    size: int,
    sha256: str,
    rev: int,
    dirty: bool,
    state: str = "",
    error: str = "",
    group_id: str = "",
) -> None:
    """Upsert a live row with the sync state given (a pulled copy, a merge, a conflict)."""
    conn.execute(
        "INSERT INTO plugin_data(account_id, scope_id, plugin_id, kind, key, value, size, sha256, rev, dirty, deleted, "
        "sensitive, updated, sync_state, sync_error, attempts, next_try, group_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, 0, 0, ?) "
        "ON CONFLICT(account_id, scope_id, plugin_id, kind, key) DO UPDATE SET value=excluded.value, "
        "size=excluded.size, sha256=excluded.sha256, rev=excluded.rev, dirty=excluded.dirty, deleted=0, "
        "updated=excluded.updated, sync_state=excluded.sync_state, sync_error=excluded.sync_error, attempts=0, "
        "next_try=0, group_id=excluded.group_id",
        (account, space, plugin, kind, key, value, int(size), sha256, int(rev), 1 if dirty else 0, time.time(),
         state, str(error or "")[:500], group_id),
    )


def delete_row(conn: Any, account: str, space: str, plugin: str, kind: str, key: str) -> None:
    conn.execute(f"DELETE FROM plugin_data WHERE {_ROW_WHERE}", (account, space, plugin, kind, key))


def make_tombstone(conn: Any, account: str, space: str, plugin: str, kind: str, key: str) -> None:
    """The row becomes a delete waiting to go out (Keep mine on a delete of mine)."""
    conn.execute(
        "UPDATE plugin_data SET value=NULL, size=0, deleted=1, dirty=1, sync_state='', sync_error='', attempts=0, "
        f"next_try=0, updated=? WHERE {_ROW_WHERE}",
        (time.time(), account, space, plugin, kind, key),
    )


def set_row_state(
    conn: Any, account: str, space: str, plugin: str, kind: str, key: str, state: str, error: str = "", *,
    rev: int | None = None, dirty: bool | None = None,
) -> None:
    sets, args = ["sync_state=?", "sync_error=?"], [state, str(error or "")[:500]]
    if rev is not None:
        sets, args = [*sets, "rev=?"], [*args, int(rev)]
    if dirty is not None:
        sets, args = [*sets, "dirty=?"], [*args, 1 if dirty else 0]
    if not state:
        sets.extend(("attempts=0", "next_try=0"))
    conn.execute(f"UPDATE plugin_data SET {', '.join(sets)} WHERE {_ROW_WHERE}",
                 (*args, account, space, plugin, kind, key))


def mark_committed(
    account: str,
    space: str,
    plugin: str,
    seq: int,
    items: Iterable[dict[str, Any]],
    *,
    bases: dict[str, tuple[str, str]] | None = None,
    units: Iterable[str] = (),
) -> dict[str, int]:
    """The server committed a changeset at ``seq``. Per item (``kind, key, op, sha256,
    deleted, updated``: the row as it went out), one compare-and-set:

    - a put clears ``dirty`` and takes ``rev=seq`` only while the row is still exactly what
      went out (same sha256, deleted flag and ``updated``); a row edited meanwhile stays
      dirty on the new base;
    - a delete removes the row only while it is still that tombstone; a row re-created
      since keeps its change on the new base.

    ``bases`` (doc key → (sha256, sealed value)) are docs whose committed content is known:
    they become the merge bases at ``seq``, also for a row edited since (its edit is on top
    of that content). The head moves when ``seq`` is the next one, and only then are
    ``units`` claimed at ``seq`` (:func:`unit_claim`: what lock requests say): past a gap, a
    teammate's change to another file of a unit may sit in it, and the pull that fills the
    gap lands this changeset and says so. Locks this PC holds on those units note the
    change. Returns ``{clean, kept}``."""
    conn = db.connect()
    clean = kept = 0
    now = time.time()
    with db.write_txn(conn):
        for item in items:
            args = (account, space, plugin, str(item["kind"]), str(item["key"]))
            if item.get("op") == "del":
                cur = conn.execute(
                    f"DELETE FROM plugin_data WHERE {_ROW_WHERE} AND deleted=1 AND updated=?",
                    (*args, float(item.get("updated") or 0.0)),
                )
                if cur.rowcount:
                    clean += 1
                    if item["kind"] == "doc":
                        base_delete(conn, account, space, plugin, str(item["key"]))
                    continue
            else:
                cur = conn.execute(
                    f"UPDATE plugin_data SET dirty=0, rev=?, sync_state='', sync_error='', attempts=0, next_try=0, "
                    f"group_id='' WHERE {_ROW_WHERE} AND deleted=0 AND sha256=? AND updated=?",
                    (int(seq), *args, str(item.get("sha256") or ""), float(item.get("updated") or 0.0)),
                )
                if cur.rowcount:
                    clean += 1
                    continue
            cur = conn.execute(f"UPDATE plugin_data SET rev=? WHERE {_ROW_WHERE}", (int(seq), *args))
            kept += cur.rowcount
        for key, (sha, value) in (bases or {}).items():
            base_put(conn, account, space, plugin, key, int(seq), sha, value)
        if head_advance(conn, account, space, plugin, int(seq), only_next=True):
            for unit in set(units):
                unit_claim(conn, account, space, plugin, unit, int(seq))
        lock_touch(conn, account, space, plugin, units, now)
    return {"clean": clean, "kept": kept}


# --------------------------------------------------------------------------- heads

_HEAD_DEFAULT: dict[str, Any] = {
    "seq": 0, "remote_seq": 0, "epoch": "", "engine": "v1", "state": "ok", "error": "", "attempts": 0,
    "next_try": 0.0, "moves": 0, "mode": "auto", "synced_at": 0.0,
}
_HEAD_FIELDS: dict[str, Any] = {
    "seq": int, "remote_seq": int, "epoch": str, "engine": str, "state": str, "error": lambda v: str(v or "")[:500],
    "attempts": int, "next_try": float, "moves": int, "mode": str, "synced_at": float,
}


def head_get(account: str, space: str, plugin: str) -> dict[str, Any]:
    r = db.connect().execute(
        "SELECT seq, remote_seq, epoch, engine, state, error, attempts, next_try, moves, mode, synced_at "
        "FROM space_heads WHERE account_id=? AND space_id=? AND plugin_id=?",
        (account, space, plugin),
    ).fetchone()
    return _row(r) if r else dict(_HEAD_DEFAULT)


def heads(account: str, space: str) -> dict[str, dict[str, Any]]:
    """``{plugin: head row}`` of every plugin of the space this PC knows a head for."""
    out = db.connect().execute(
        "SELECT plugin_id, seq, remote_seq, epoch, engine, state, error, attempts, next_try, moves, mode, synced_at "
        "FROM space_heads WHERE account_id=? AND space_id=? ORDER BY plugin_id",
        (account, space),
    ).fetchall()
    return {str(r["plugin_id"]): {k: r[k] for k in r.keys() if k != "plugin_id"} for r in out}


def head_put(account: str, space: str, plugin: str, *, conn: Any = None, **fields: Any) -> None:
    """Set ``fields`` on the head row in one statement (other names are ignored). ``seq``
    never moves back here: :func:`head_reset` does that."""
    cols = [name for name in fields if name in _HEAD_FIELDS]
    if not cols:
        return
    values = [_HEAD_FIELDS[name](fields[name]) for name in cols]
    sets = ["seq=MAX(space_heads.seq, excluded.seq)" if c == "seq" else f"{c}=excluded.{c}" for c in cols]
    sql = (f"INSERT INTO space_heads(account_id, space_id, plugin_id, {', '.join(cols)}) "
           f"VALUES (?, ?, ?{', ?' * len(cols)}) ON CONFLICT(account_id, space_id, plugin_id) DO UPDATE SET "
           f"{', '.join(sets)}")
    if conn is not None:
        conn.execute(sql, (account, space, plugin, *values))
        return
    c = db.connect()
    with db.write_txn(c):
        c.execute(sql, (account, space, plugin, *values))


def head_advance(conn: Any, account: str, space: str, plugin: str, seq: int, *, only_next: bool = False) -> bool:
    """The changeset at ``seq`` is applied here. ``only_next``: move only when it is exactly
    the next one (a commit of our own past a gap waits for the pull that fills it)."""
    conn.execute(
        "INSERT INTO space_heads(account_id, space_id, plugin_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        (account, space, plugin),
    )
    cond = "seq=?" if only_next else "seq<?"
    cur = conn.execute(
        f"UPDATE space_heads SET seq=?, remote_seq=MAX(remote_seq, ?) WHERE account_id=? AND space_id=? "
        f"AND plugin_id=? AND {cond}",
        (int(seq), int(seq), account, space, plugin, int(seq) - 1 if only_next else int(seq)),
    )
    return cur.rowcount > 0


def head_reset(account: str, space: str, plugin: str, seq: int = 0, *, conn: Any = None) -> None:
    """Exactly ``seq``, back or forward (a full resync lands on the server's head)."""
    sql = ("INSERT INTO space_heads(account_id, space_id, plugin_id, seq) VALUES (?, ?, ?, ?) "
           "ON CONFLICT(account_id, space_id, plugin_id) DO UPDATE SET seq=excluded.seq")
    if conn is not None:
        conn.execute(sql, (account, space, plugin, int(seq)))
        return
    c = db.connect()
    with db.write_txn(c):
        c.execute(sql, (account, space, plugin, int(seq)))


def reset_epoch(account: str, space: str, epoch: str) -> None:
    """The space's data was reset on the server: the versions this PC kept of the old data
    go (changesets, blocked items, locks, merge bases, unit seqs) and every plugin's head
    starts again at 0 under the new epoch. The rows, and the conflicts' losing versions of
    mine, stay for the person's choice (:func:`backend.store.repos.plugin_data.settle_reset`:
    Upload my copy sends mine, Clear my copy drops it)."""
    conn = db.connect()
    with db.write_txn(conn):
        for table in ("space_changesets", "space_incoming_blocked", "space_locks", "plugin_data_base", "space_units"):
            conn.execute(f"DELETE FROM {table} WHERE account_id=? AND space_id=?", (account, space))
        conn.execute("UPDATE space_heads SET seq=0, remote_seq=0, epoch=?, moves=0, attempts=0, next_try=0 "
                     "WHERE account_id=? AND space_id=?", (epoch, account, space))
        _queues_drop(conn, account, space)


def backoff(account: str, space: str, plugin: str, state: str, error: str = "", *, after: float | None = None,
            now: float | None = None) -> float:
    """A round failed (offline, error, the server said retry): the next one waits 30 s,
    2 min, 5 min, then 15 min, or ``after`` seconds when the server said so. Returns
    ``next_try``."""
    now = _now(now)
    conn = db.connect()
    with db.write_txn(conn):
        r = conn.execute(
            "SELECT attempts FROM space_heads WHERE account_id=? AND space_id=? AND plugin_id=?",
            (account, space, plugin),
        ).fetchone()
        attempts = (int(r[0]) if r else 0) + 1
        wait = float(after) if after is not None else BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
        head_put(account, space, plugin, conn=conn, state=state, error=error, attempts=attempts,
                 next_try=now + max(0.0, wait))
    return now + max(0.0, wait)


def head_ok(account: str, space: str, plugin: str, *, now: float | None = None, **fields: Any) -> None:
    """A round worked: no backoff, state ``ok``."""
    head_put(account, space, plugin, state="ok", error="", attempts=0, next_try=0.0, synced_at=_now(now), **fields)


# --------------------------------------------------------------------------- changesets


def cs_add(
    account: str,
    space: str,
    plugin: str,
    cs_id: str,
    *,
    base_seq: int,
    items: list[dict[str, Any]],
    group_id: str = "",
    part: int = 1,
    last: bool = True,
    message: str = "",
    actor: str = "user",
    ducky: str = "",
    author: str = "",
    author_pc: str = "",
    conn: Any = None,
) -> None:
    """Persist a changeset before anything is uploaded (state ``pending``). ``author`` /
    ``author_pc``: the groupmate (and PC) a live group's leader sends it for."""
    now = time.time()
    args = (account, space, plugin, cs_id, int(base_seq), group_id, int(part), 1 if last else 0, str(message)[:2000],
            actor, ducky, str(author)[:120], str(author_pc)[:64], json.dumps(items, separators=(",", ":")), now, now)
    sql = ("INSERT INTO space_changesets(account_id, space_id, plugin_id, cs_id, base_seq, group_id, part, last, "
           "message, actor, ducky, author, author_pc, items_json, created, updated) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    if conn is not None:
        conn.execute(sql, args)
        return
    c = db.connect()
    with db.write_txn(c):
        c.execute(sql, args)


def _cs(r: Any) -> dict[str, Any]:
    out = _row(r)
    try:
        out["items"] = json.loads(out.pop("items_json") or "[]")
    except ValueError:
        out["items"] = []
    out["last"] = bool(out["last"])
    return out


def cs_get(account: str, space: str, cs_id: str) -> dict[str, Any] | None:
    r = db.connect().execute(
        "SELECT * FROM space_changesets WHERE account_id=? AND space_id=? AND cs_id=?", (account, space, cs_id)
    ).fetchone()
    return _cs(r) if r else None


def cs_open(account: str, space: str, plugin: str | None = None) -> list[dict[str, Any]]:
    """Changesets not yet answered (pending or pushing), oldest first."""
    sql, args = ("SELECT * FROM space_changesets WHERE account_id=? AND space_id=? AND state IN ('pending', 'pushing')",
                 [account, space])
    if plugin is not None:
        sql, args = sql + " AND plugin_id=?", [*args, plugin]
    return [_cs(r) for r in db.connect().execute(sql + " ORDER BY created, part", args).fetchall()]


def cs_items_open(account: str, space: str, plugin: str) -> set[tuple[str, str]]:
    """(kind, key) of every item an unanswered changeset carries."""
    out: set[tuple[str, str]] = set()
    for cs in cs_open(account, space, plugin):
        out.update((str(i.get("kind")), str(i.get("key"))) for i in cs["items"])
    return out


def cs_set(account: str, space: str, cs_id: str, **fields: Any) -> None:
    allowed = {"state": str, "seq": int, "error": lambda v: str(v or "")[:500], "items_json": str, "base_seq": int,
               "message": str}
    if "items" in fields:
        fields["items_json"] = json.dumps(fields.pop("items"), separators=(",", ":"))
    cols = [c for c in fields if c in allowed]
    if not cols:
        return
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            f"UPDATE space_changesets SET {', '.join(f'{c}=?' for c in cols)}, updated=? "
            "WHERE account_id=? AND space_id=? AND cs_id=?",
            (*[allowed[c](fields[c]) for c in cols], time.time(), account, space, cs_id),
        )


def cs_delete(account: str, space: str, cs_id: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM space_changesets WHERE account_id=? AND space_id=? AND cs_id=?",
                     (account, space, cs_id))


def cs_list(account: str, space: str, plugin: str, limit: int = 50) -> list[dict[str, Any]]:
    """Recent changesets of this PC, newest first (the drawer's outgoing list)."""
    out = db.connect().execute(
        "SELECT * FROM space_changesets WHERE account_id=? AND space_id=? AND plugin_id=? ORDER BY created DESC "
        "LIMIT ?",
        (account, space, plugin, limit),
    ).fetchall()
    return [_cs(r) for r in out]


# --------------------------------------------------------------------------- conflicts


def conflict_put(
    conn: Any,
    account: str,
    space: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    unit: str = "",
    reason: str = "changed",
    mine_value: str | None = None,
    mine_path: str = "",
    mine_sha: str = "",
    mine_size: int = 0,
    mine_deleted: bool = False,
    base_seq: int = 0,
    theirs_seq: int = 0,
    theirs_by: str = "",
    theirs_at: float = 0.0,
    theirs_deleted: bool = False,
) -> None:
    """Record a losing version. It is only ever called for an edit made here and not sent
    yet, so it replaces an older unresolved one of the same item: the newest edit is the
    one the person would keep."""
    conn.execute(
        "INSERT INTO space_conflicts(account_id, space_id, plugin_id, kind, key, unit, reason, mine_value, mine_path, "
        "mine_sha, mine_size, mine_deleted, base_seq, theirs_seq, theirs_by, theirs_at, theirs_deleted, created) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(account_id, space_id, plugin_id, kind, key) DO UPDATE SET unit=excluded.unit, "
        "reason=excluded.reason, mine_value=excluded.mine_value, mine_path=excluded.mine_path, "
        "mine_sha=excluded.mine_sha, mine_size=excluded.mine_size, mine_deleted=excluded.mine_deleted, "
        "base_seq=excluded.base_seq, theirs_seq=excluded.theirs_seq, theirs_by=excluded.theirs_by, "
        "theirs_at=excluded.theirs_at, theirs_deleted=excluded.theirs_deleted, created=excluded.created",
        (account, space, plugin, kind, key, unit, reason, mine_value, mine_path, mine_sha, int(mine_size),
         1 if mine_deleted else 0, int(base_seq), int(theirs_seq), theirs_by, float(theirs_at),
         1 if theirs_deleted else 0, time.time()),
    )


def conflict_get(account: str, space: str, plugin: str, kind: str, key: str, *,
                 conn: Any = None) -> dict[str, Any] | None:
    r = (conn or db.connect()).execute(
        "SELECT * FROM space_conflicts WHERE account_id=? AND space_id=? AND plugin_id=? AND kind=? AND key=?",
        (account, space, plugin, kind, key),
    ).fetchone()
    return _row(r) if r else None


def conflicts(account: str, space: str, plugin: str | None = None) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM space_conflicts WHERE account_id=? AND space_id=?", [account, space]
    if plugin is not None:
        sql, args = sql + " AND plugin_id=?", [*args, plugin]
    return [_row(r) for r in db.connect().execute(sql + " ORDER BY created", args).fetchall()]


def conflict_delete(conn: Any, account: str, space: str, plugin: str, kind: str, key: str) -> None:
    conn.execute(
        "DELETE FROM space_conflicts WHERE account_id=? AND space_id=? AND plugin_id=? AND kind=? AND key=?",
        (account, space, plugin, kind, key),
    )


# --------------------------------------------------------------------------- incoming set aside


def blocked_put(conn: Any, account: str, space: str, plugin: str, kind: str, key: str, *, unit: str, seq: int,
                error: str, now: float | None = None) -> dict[str, Any]:
    """An incoming item couldn't be applied: it waits ``min(2**attempts * 30 s, 1 h)``.
    Returns ``{attempts, next_try}``."""
    r = conn.execute(
        "SELECT attempts FROM space_incoming_blocked WHERE account_id=? AND space_id=? AND plugin_id=? AND kind=? "
        "AND key=?",
        (account, space, plugin, kind, key),
    ).fetchone()
    attempts = (int(r[0]) if r else 0) + 1
    next_try = _now(now) + min(2 ** attempts * 30.0, BLOCKED_MAX_WAIT_S)
    conn.execute(
        "INSERT INTO space_incoming_blocked(account_id, space_id, plugin_id, kind, key, unit, seq, error, attempts, "
        "next_try) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, space_id, plugin_id, kind, key) "
        "DO UPDATE SET unit=excluded.unit, seq=MAX(space_incoming_blocked.seq, excluded.seq), error=excluded.error, "
        "attempts=excluded.attempts, next_try=excluded.next_try",
        (account, space, plugin, kind, key, unit, int(seq), str(error or "")[:500], attempts, next_try),
    )
    return {"attempts": attempts, "next_try": next_try}


def blocked_delete(conn: Any, account: str, space: str, plugin: str, kind: str, key: str) -> None:
    conn.execute(
        "DELETE FROM space_incoming_blocked WHERE account_id=? AND space_id=? AND plugin_id=? AND kind=? AND key=?",
        (account, space, plugin, kind, key),
    )


def blocked(account: str, space: str, plugin: str | None = None, *, due: float | None = None) -> list[dict[str, Any]]:
    """Items set aside; ``due``: only those whose wait is over at that time."""
    sql, args = "SELECT * FROM space_incoming_blocked WHERE account_id=? AND space_id=?", [account, space]
    if plugin is not None:
        sql, args = sql + " AND plugin_id=?", [*args, plugin]
    if due is not None:
        sql, args = sql + " AND next_try<=?", [*args, float(due)]
    return [_row(r) for r in db.connect().execute(sql + " ORDER BY next_try", args).fetchall()]


def blocked_retry_now(account: str, space: str, plugin: str, kind: str, key: str) -> bool:
    """Re-download: the item is retried on the next round."""
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute(
            "UPDATE space_incoming_blocked SET next_try=0 WHERE account_id=? AND space_id=? AND plugin_id=? AND kind=? "
            "AND key=?",
            (account, space, plugin, kind, key),
        )
    return cur.rowcount > 0


# --------------------------------------------------------------------------- locks


def _lock_row(r: Any, now: float) -> dict[str, Any]:
    out = _row(r)
    out["mine"] = bool(out["mine"])
    out["lease_left"] = max(0.0, float(out["lease_left_at"]) - now)
    return out


def locks(account: str, space: str, plugin: str | None = None, *, now: float | None = None,
          live: bool = True) -> list[dict[str, Any]]:
    """The lock cache; ``live``: only leases not run out on this PC's clock."""
    now = _now(now)
    sql, args = "SELECT * FROM space_locks WHERE account_id=? AND space_id=?", [account, space]
    if plugin is not None:
        sql, args = sql + " AND plugin_id=?", [*args, plugin]
    if live:
        sql, args = sql + " AND lease_left_at>?", [*args, now]
    return [_lock_row(r, now) for r in db.connect().execute(sql + " ORDER BY plugin_id, unit", args).fetchall()]


def foreign_lock(account: str, space: str, plugin: str, unit: str, *, now: float | None = None,
                 conn: Any = None) -> dict[str, Any] | None:
    """A live lock someone else holds on ``unit`` (what writers check), or None."""
    now = _now(now)
    r = (conn or db.connect()).execute(
        "SELECT * FROM space_locks WHERE account_id=? AND space_id=? AND plugin_id=? AND unit=? AND mine=0 "
        "AND lease_left_at>?",
        (account, space, plugin, unit, now),
    ).fetchone()
    return _lock_row(r, now) if r else None


def lock_put(account: str, space: str, plugin: str, unit: str, *, name: str = "", holder_user: str = "",
             holder_name: str = "", holder_device: str = "", since: float = 0.0, lease_left: float = 0.0,
             fence: int = 0, mine: bool = False, reason: str = "", now: float | None = None,
             conn: Any = None) -> None:
    """One lock as the server said (``lease_left`` seconds from now). A lock of mine keeps its
    ``reason`` (unless given) and ``touched``; ``renewed`` is now."""
    now = _now(now)
    sql = (
        "INSERT INTO space_locks(account_id, space_id, plugin_id, unit, name, holder_user, holder_name, holder_device, "
        "since, lease_left_at, fence, mine, reason, renewed, touched) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(account_id, space_id, plugin_id, unit) DO UPDATE SET name=CASE WHEN excluded.name<>'' "
        "THEN excluded.name ELSE space_locks.name END, holder_user=excluded.holder_user, "
        "holder_name=excluded.holder_name, holder_device=excluded.holder_device, since=excluded.since, "
        "lease_left_at=excluded.lease_left_at, fence=excluded.fence, mine=excluded.mine, "
        "reason=CASE WHEN excluded.reason<>'' THEN excluded.reason WHEN excluded.mine=1 AND space_locks.mine=1 "
        "THEN space_locks.reason ELSE '' END, "
        "renewed=CASE WHEN excluded.mine=1 THEN excluded.renewed ELSE 0 END, "
        "touched=CASE WHEN excluded.mine=1 AND space_locks.mine=1 THEN space_locks.touched ELSE excluded.touched END"
    )
    args = (account, space, plugin, unit, name, holder_user, holder_name, holder_device, float(since),
            now + max(0.0, float(lease_left)), int(fence), 1 if mine else 0, reason if mine else "",
            now if mine else 0.0, now if mine else 0.0)
    if conn is not None:
        conn.execute(sql, args)
        return
    c = db.connect()
    with db.write_txn(c):
        c.execute(sql, args)


def locks_replace(account: str, space: str, live: Iterable[dict[str, Any]], *, now: float | None = None,
                  plugins: Iterable[str] | None = None,
                  asked_at: float | None = None) -> dict[str, list[dict[str, Any]]]:
    """The server's list of live locks in the space (``plugins``: only those plugins'). Rows
    not in it go, except locks of mine taken or renewed after ``asked_at`` (when the list
    was asked for: it can't know them yet). Returns ``{lost, freed}``: locks of mine that
    went (and who holds the unit now, if anyone), and units whose foreign lock went."""
    now = _now(now)
    asked_at = now if asked_at is None else float(asked_at)
    live = list(live)
    scope = None if plugins is None else set(plugins)
    conn = db.connect()
    lost: list[dict[str, Any]] = []
    freed: list[dict[str, Any]] = []
    with db.write_txn(conn):
        old = {(r["plugin_id"], r["unit"]): _lock_row(r, now) for r in conn.execute(
            "SELECT * FROM space_locks WHERE account_id=? AND space_id=?", (account, space)).fetchall()
            if scope is None or r["plugin_id"] in scope}
        seen: set[tuple[str, str]] = set()
        for lk in live:
            k = (str(lk.get("plugin") or ""), str(lk.get("unit") or ""))
            if not k[0] or not k[1] or (scope is not None and k[0] not in scope):
                continue
            seen.add(k)
            mine = bool(lk.get("mine"))
            lock_put(account, space, k[0], k[1], name=str(lk.get("name") or ""),
                     holder_user=str(lk.get("user") or ""), holder_name=str(lk.get("user_name") or ""),
                     holder_device=str(lk.get("device") or ""), since=float(lk.get("since") or 0.0),
                     lease_left=float(lk.get("lease_left") or 0.0), fence=int(lk.get("fence") or 0), mine=mine,
                     reason=str(lk.get("reason") or "") if mine else "", now=now, conn=conn)
            before = old.get(k)
            if before and before["mine"] and not mine:
                lost.append({**before, "now_holder": str(lk.get("user_name") or lk.get("user") or ""),
                             "now_user": str(lk.get("user") or ""), "now_device": str(lk.get("device") or "")})
            elif before and not before["mine"] and mine:
                freed.append(before)
        for k, before in old.items():
            if k in seen or (before["mine"] and float(before["renewed"]) > asked_at):
                continue
            conn.execute("DELETE FROM space_locks WHERE account_id=? AND space_id=? AND plugin_id=? AND unit=?",
                         (account, space, *k))
            if before["mine"] and before["lease_left"] > 0:
                lost.append({**before, "now_holder": ""})
            elif not before["mine"]:
                freed.append(before)
    return {"lost": lost, "freed": freed}


def lock_delete(account: str, space: str, plugin: str, units: Iterable[str]) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        for unit in units:
            conn.execute("DELETE FROM space_locks WHERE account_id=? AND space_id=? AND plugin_id=? AND unit=?",
                         (account, space, plugin, unit))


def lock_touch(conn: Any, account: str, space: str, plugin: str, units: Iterable[str], at: float) -> None:
    """A change to these units was committed: auto locks of mine count their idle time from now."""
    for unit in set(units):
        conn.execute(
            "UPDATE space_locks SET touched=? WHERE account_id=? AND space_id=? AND plugin_id=? AND unit=? AND mine=1",
            (float(at), account, space, plugin, unit),
        )


# --------------------------------------------------------------------------- presence + activity


def presence_get(account: str, space: str) -> dict[str, Any]:
    r = db.connect().execute(
        "SELECT roster_json, updated, sent_sha, sent_at FROM space_presence WHERE account_id=? AND space_id=?",
        (account, space),
    ).fetchone()
    if not r:
        return {"roster": {}, "updated": 0.0, "sent_sha": "", "sent_at": 0.0}
    try:
        roster = json.loads(r["roster_json"] or "{}")
    except ValueError:
        roster = {}
    return {"roster": roster, "updated": float(r["updated"]), "sent_sha": str(r["sent_sha"]),
            "sent_at": float(r["sent_at"])}


def presence_put(account: str, space: str, roster: dict[str, Any] | None = None, *, sent_sha: str | None = None,
                 now: float | None = None) -> None:
    """The roster the server answered with, and/or the beat just sent (its sha)."""
    now = _now(now)
    sets, args = [], []
    if roster is not None:
        sets += ["roster_json=excluded.roster_json", "updated=excluded.updated"]
    if sent_sha is not None:
        sets += ["sent_sha=excluded.sent_sha", "sent_at=excluded.sent_at"]
    if not sets:
        return
    args = [account, space, json.dumps(roster or {}, separators=(",", ":")), now, sent_sha or "",
            now if sent_sha is not None else 0.0]
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO space_presence(account_id, space_id, roster_json, updated, sent_sha, sent_at) "
            f"VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, space_id) DO UPDATE SET {', '.join(sets)}",
            args,
        )


def activity_note(account: str, space: str, plugin: str, unit: str, *, actor: str = "user", ducky: str = "",
                  at: float | None = None, conn: Any = None) -> None:
    """A unit changed here just now (writers in every process, the folder watcher)."""
    at = _now(at)
    sql = ("INSERT INTO space_activity(account_id, space_id, plugin_id, unit, actor, ducky, since, at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, space_id, plugin_id, unit, actor, ducky) DO UPDATE "
           "SET since=CASE WHEN space_activity.at<excluded.at-? THEN excluded.since ELSE space_activity.since END, "
           "at=excluded.at")
    args = (account, space, plugin, unit, actor, ducky, at, at, ACTIVITY_KEEP_S)
    if conn is not None:
        conn.execute(sql, args)
        return
    c = db.connect()
    with db.write_txn(c):
        c.execute(sql, args)


def activity(account: str, space: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """Units changed in the last 2 minutes, newest first."""
    out = db.connect().execute(
        "SELECT plugin_id, unit, actor, ducky, since, at FROM space_activity WHERE account_id=? AND space_id=? "
        "AND at>=? ORDER BY at DESC",
        (account, space, _now(now) - ACTIVITY_KEEP_S),
    ).fetchall()
    return [_row(r) for r in out]


def last_activity(account: str, space: str) -> float:
    r = db.connect().execute(
        "SELECT MAX(at) FROM space_activity WHERE account_id=? AND space_id=?", (account, space)
    ).fetchone()
    return float(r[0] or 0.0) if r else 0.0


# --------------------------------------------------------------------------- write groups


def group_open(group_id: str, plugin: str, *, actor: str = "user", ducky: str = "", message: str = "",
               at: float | None = None) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO space_groups(group_id, plugin_id, open, actor, ducky, message, started, closed) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, 0) ON CONFLICT(group_id) DO NOTHING",
            (group_id, plugin, actor, ducky, str(message)[:500], _now(at)),
        )


def group_close(group_id: str, *, at: float | None = None) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("UPDATE space_groups SET open=0, closed=? WHERE group_id=? AND open=1", (_now(at), group_id))


def groups(group_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = sorted({g for g in group_ids if g})
    if not ids:
        return {}
    out = db.connect().execute(
        f"SELECT * FROM space_groups WHERE group_id IN ({', '.join('?' * len(ids))})", ids
    ).fetchall()
    return {str(r["group_id"]): _row(r) for r in out}


def groups_close_stale(older_than: float, *, now: float | None = None) -> int:
    """Close groups open longer than ``older_than`` seconds: the call that opened them never
    returned (the process died, or it hung), and their rows must not wait for it forever."""
    conn = db.connect()
    at = _now(now)
    with db.write_txn(conn):
        return conn.execute("UPDATE space_groups SET open=0, closed=? WHERE open=1 AND started<?",
                            (at, at - float(older_than))).rowcount


def groups_closed(account: str) -> dict[str, float]:
    """``{space: when the newest closed write group with changes still to send closed}``:
    those changes go out a few seconds after their call returned."""
    out = db.connect().execute(
        "SELECT d.scope_id, MAX(g.closed) FROM plugin_data d JOIN space_groups g ON g.group_id=d.group_id "
        "WHERE d.account_id=? AND d.dirty=1 AND d.group_id<>'' AND g.open=0 GROUP BY d.scope_id",
        (account,),
    ).fetchall()
    return {str(r[0]): float(r[1] or 0.0) for r in out}


def group_add_closed(conn: Any, group_id: str, plugin: str, *, actor: str = "user", ducky: str = "",
                     message: str = "", at: float | None = None) -> None:
    """A group that is already closed (rows a merge wrote, inside the caller's transaction)."""
    at = _now(at)
    conn.execute(
        "INSERT INTO space_groups(group_id, plugin_id, open, actor, ducky, message, started, closed) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?) ON CONFLICT(group_id) DO NOTHING",
        (group_id, plugin, actor, ducky, str(message)[:500], at, at),
    )


# --------------------------------------------------------------------------- lock requests


def lock_request_add(account: str, space: str, plugin: str, unit: str, name: str = "") -> None:
    """A writer needs ``unit`` locked before it writes (a ``required`` prefix)."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO space_lock_requests(account_id, space_id, plugin_id, unit, name, state, answer, created) "
            "VALUES (?, ?, ?, ?, ?, 'pending', '', ?) ON CONFLICT(account_id, space_id, plugin_id, unit) DO UPDATE "
            "SET name=excluded.name, state='pending', answer='', created=excluded.created",
            (account, space, plugin, unit, name, time.time()),
        )


def lock_request_get(account: str, space: str, plugin: str, unit: str) -> dict[str, Any] | None:
    r = db.connect().execute(
        "SELECT * FROM space_lock_requests WHERE account_id=? AND space_id=? AND plugin_id=? AND unit=?",
        (account, space, plugin, unit),
    ).fetchone()
    return _row(r) if r else None


def lock_requests_pending(account: str, space: str | None = None) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM space_lock_requests WHERE account_id=? AND state='pending'", [account]
    if space is not None:
        sql, args = sql + " AND space_id=?", [*args, space]
    return [_row(r) for r in db.connect().execute(sql + " ORDER BY created", args).fetchall()]


def lock_request_answer(account: str, space: str, plugin: str, unit: str, state: str, answer: str = "") -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "UPDATE space_lock_requests SET state=?, answer=? WHERE account_id=? AND space_id=? AND plugin_id=? "
            "AND unit=?",
            (state, str(answer or "")[:500], account, space, plugin, unit),
        )


# --------------------------------------------------------------------------- merge bases + unit seqs


def base_get(account: str, space: str, plugin: str, key: str, *, conn: Any = None) -> dict[str, Any] | None:
    r = (conn or db.connect()).execute(
        "SELECT seq, sha256, value FROM plugin_data_base WHERE account_id=? AND space_id=? AND plugin_id=? AND key=?",
        (account, space, plugin, key),
    ).fetchone()
    return _row(r) if r else None


def base_put(conn: Any, account: str, space: str, plugin: str, key: str, seq: int, sha256: str,
             value: str | None) -> None:
    conn.execute(
        "INSERT INTO plugin_data_base(account_id, space_id, plugin_id, key, seq, sha256, value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, space_id, plugin_id, key) DO UPDATE SET "
        "seq=excluded.seq, sha256=excluded.sha256, value=excluded.value",
        (account, space, plugin, key, int(seq), sha256, value),
    )


def base_delete(conn: Any, account: str, space: str, plugin: str, key: str) -> None:
    conn.execute("DELETE FROM plugin_data_base WHERE account_id=? AND space_id=? AND plugin_id=? AND key=?",
                 (account, space, plugin, key))


def unit_seen(conn: Any, account: str, space: str, plugin: str, unit: str, seq: int) -> None:
    """A changeset at ``seq`` touching ``unit`` is applied here."""
    if not unit:
        return
    conn.execute(
        "INSERT INTO space_units(account_id, space_id, plugin_id, unit, seq) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(account_id, space_id, plugin_id, unit) DO UPDATE SET seq=MAX(space_units.seq, excluded.seq)",
        (account, space, plugin, unit, int(seq)),
    )


def unit_set_aside(conn: Any, account: str, space: str, plugin: str, unit: str) -> bool:
    """An incoming item of ``unit`` is set aside here (:func:`blocked_put`)."""
    return conn.execute("SELECT 1 FROM space_incoming_blocked WHERE account_id=? AND space_id=? AND plugin_id=? "
                        "AND unit=? LIMIT 1", (account, space, plugin, unit)).fetchone() is not None


def unit_claim(conn: Any, account: str, space: str, plugin: str, unit: str, seq: int) -> bool:
    """Every change to ``unit`` up to ``seq`` is here: :func:`unit_seen`, which lock requests
    say, unless an incoming item of the unit is set aside (the unit isn't whole: a lock on
    it would be granted on a stale copy). The landing that makes it whole again claims it
    then. Returns whether it was claimed."""
    if not unit or unit_set_aside(conn, account, space, plugin, unit):
        return False
    unit_seen(conn, account, space, plugin, unit, seq)
    return True


def unit_seqs(account: str, space: str, plugin: str, units: Iterable[str]) -> dict[str, int]:
    """``{unit: applied seq}`` (0 for a unit never applied here)."""
    want = list(dict.fromkeys(units))
    out = {u: 0 for u in want}
    if not want:
        return out
    rows = db.connect().execute(
        f"SELECT unit, seq FROM space_units WHERE account_id=? AND space_id=? AND plugin_id=? "
        f"AND unit IN ({', '.join('?' * len(want))})",
        (account, space, plugin, *want),
    ).fetchall()
    for r in rows:
        out[str(r[0])] = int(r[1])
    return out


# --------------------------------------------------------------------------- lost access


# An unresolved conflict whose losing version is mine and lives only there: the row holds
# theirs (a doc clash, a file kept both ways), so nothing else on this PC has mine. A
# folder unit's conflict keeps mine on disk (it counts as a changed unit instead).
_MINE_ONLY = (
    "c.account_id=? AND c.space_id=? AND c.kind IN ('doc', 'asset') AND c.mine_deleted=0 "
    "AND (c.mine_value IS NOT NULL OR c.mine_path<>'') "
    "AND NOT EXISTS (SELECT 1 FROM plugin_data d WHERE d.account_id=c.account_id AND d.scope_id=c.space_id "
    "AND d.plugin_id=c.plugin_id AND d.kind=c.kind AND d.key=c.key AND d.dirty=1)"
)


def pending_unsent(account: str, space: str) -> int:
    """Changes of this PC a lost space never got: live dirty rows (never sensitive),
    conflicts whose losing version is mine (:func:`unsent_conflicts`) and folder units
    changed here (held back ones too). Once the person chose what to do with them (saved
    or discarded), only what changed after that counts."""
    from backend.store.repos import folder_files

    conn = db.connect()
    r = conn.execute("SELECT at FROM space_lost_choice WHERE account_id=? AND space_id=?", (account, space)).fetchone()
    since = float(r[0]) if r else None
    rows_sql, rows_args = ("SELECT COUNT(*) FROM plugin_data WHERE account_id=? AND scope_id=? AND dirty=1 "
                           "AND deleted=0 AND sensitive=0", [account, space])
    mine_sql, mine_args = f"SELECT COUNT(*) FROM space_conflicts c WHERE {_MINE_ONLY}", [account, space]
    if since is not None:
        rows_sql, rows_args = rows_sql + " AND updated>?", [*rows_args, since]
        mine_sql, mine_args = mine_sql + " AND c.created>?", [*mine_args, since]
    return (int(conn.execute(rows_sql, rows_args).fetchone()[0]) + int(conn.execute(mine_sql, mine_args).fetchone()[0])
            + folder_files.unsent_units(account, space, since=since))


def unsent_conflicts(account: str, space: str) -> list[dict[str, Any]]:
    """Unresolved conflicts whose losing version is mine and kept only there: a doc's
    ``mine_value`` or a file's sealed side copy at ``mine_path``."""
    out = db.connect().execute(
        f"SELECT c.* FROM space_conflicts c WHERE {_MINE_ONLY} ORDER BY c.plugin_id, c.kind, c.key", (account, space)
    ).fetchall()
    return [_row(r) for r in out]


def unsent_rows(account: str, space: str) -> list[dict[str, Any]]:
    out = db.connect().execute(
        f"SELECT {_ROW_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND dirty=1 AND deleted=0 "
        "AND sensitive=0 ORDER BY plugin_id, kind, key",
        (account, space),
    ).fetchall()
    return [_row(r) for r in out]


def lost_choice_put(account: str, space: str, choice: str) -> None:
    if choice not in LOST_CHOICES:
        raise ValueError(f"unknown choice: {choice!r}")
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO space_lost_choice(account_id, space_id, choice, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id, space_id) DO UPDATE SET choice=excluded.choice, at=excluded.at",
            (account, space, choice, time.time()),
        )


def lost_choice_get(account: str, space: str) -> str:
    r = db.connect().execute("SELECT choice FROM space_lost_choice WHERE account_id=? AND space_id=?",
                             (account, space)).fetchone()
    return str(r[0]) if r else ""


def lost_choice_clear(account: str, space: str) -> None:
    """Access came back: a later loss asks again."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM space_lost_choice WHERE account_id=? AND space_id=?", (account, space))


# --------------------------------------------------------------------------- device + change log


def device_id() -> str:
    """This install's device id: a random id made once and kept (never a hardware id).
    The first process to ask makes it; every process reads the same one."""
    conn = db.connect()
    r = conn.execute("SELECT value FROM meta WHERE key=?", (DEVICE_KEY,)).fetchone()
    if r:
        return str(r[0])
    with db.write_txn(conn):
        conn.execute("INSERT OR IGNORE INTO meta(key, value, updated) VALUES (?, ?, ?)",
                     (DEVICE_KEY, uuid.uuid4().hex, time.time()))
    return str(conn.execute("SELECT value FROM meta WHERE key=?", (DEVICE_KEY,)).fetchone()[0])


def change_log_bump(conn: Any, plugin: str, keys: Iterable[str]) -> int:
    """A pull changed these keys of ``plugin`` (inside the apply's transaction): the
    plugin's epoch row moves on, so every process that loaded the plugin can tell its
    backend. Keeps the last :data:`CHANGE_LOG_KEEP` entries; ``trimmed`` is the newest
    epoch dropped. Returns the new epoch."""
    k = CHANGE_LOG_PREFIX + plugin
    r = conn.execute("SELECT value FROM workspace_state WHERE key=?", (k,)).fetchone()
    try:
        doc = json.loads(r[0]) if r else {}
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    epoch = int(doc.get("epoch") or 0) + 1
    names = sorted(set(keys))
    changes = [c for c in doc.get("changes") or [] if isinstance(c, list) and len(c) == 2]
    changes.append([epoch, names if len(names) <= CHANGE_KEYS_MAX else None])
    trimmed = int(doc.get("trimmed") or 0)
    if len(changes) > CHANGE_LOG_KEEP:
        trimmed = int(changes[-CHANGE_LOG_KEEP - 1][0])
        changes = changes[-CHANGE_LOG_KEEP:]
    doc.update(epoch=epoch, changes=changes, trimmed=trimmed)
    conn.execute(
        "INSERT INTO workspace_state(key, value, updated) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (k, json.dumps(doc, separators=(",", ":")), time.time()),
    )
    return epoch


def change_log(plugin: str) -> dict[str, Any]:
    """``{epoch, changes: [[epoch, keys | None]...], trimmed}`` (``None``: too many keys to list)."""
    r = db.connect().execute("SELECT value FROM workspace_state WHERE key=?",
                             (CHANGE_LOG_PREFIX + plugin,)).fetchone()
    try:
        doc = json.loads(r[0]) if r else {}
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    return {"epoch": int(doc.get("epoch") or 0), "changes": list(doc.get("changes") or []),
            "trimmed": int(doc.get("trimmed") or 0)}


# --------------------------------------------------------------------------- waiting incoming + switch progress


def _state_doc(key: str) -> dict[str, Any]:
    r = db.connect().execute("SELECT value FROM workspace_state WHERE key=?", (key,)).fetchone()
    try:
        doc = json.loads(r[0]) if r else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def _state_put(key: str, doc: dict[str, Any] | None) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        if doc is None:
            conn.execute("DELETE FROM workspace_state WHERE key=?", (key,))
            return
        conn.execute(
            "INSERT INTO workspace_state(key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (key, json.dumps(doc, separators=(",", ":")), time.time()),
        )


def incoming_queue_get(account: str, space: str, plugin: str) -> dict[str, Any]:
    """Changesets of ``plugin`` pulled but not applied, waiting behind "Update now":
    ``{count, by: [names], units: [unit ids], seq}`` (``count`` 0: none)."""
    doc = _state_doc(f"{INCOMING_PREFIX}{account}:{space}:{plugin}")
    return {"count": int(doc.get("count") or 0), "by": [str(b) for b in doc.get("by") or []],
            "units": [str(u) for u in doc.get("units") or []], "seq": int(doc.get("seq") or 0)}


def incoming_queue_put(account: str, space: str, plugin: str, *, count: int = 0, by: Iterable[str] = (),
                       units: Iterable[str] = (), seq: int = 0) -> None:
    """What waits behind "Update now" now (``count`` 0 clears it)."""
    key = f"{INCOMING_PREFIX}{account}:{space}:{plugin}"
    if count <= 0:
        _state_put(key, None)
        return
    _state_put(key, {"count": int(count), "by": list(dict.fromkeys(b for b in by if b))[:8],
                     "units": sorted(set(units))[:2000], "seq": int(seq)})


def binding_get(plugin: str, *, now: float | None = None) -> dict[str, Any] | None:
    """A switch of ``plugin`` getting a team's folder copy right now: ``{team, pct, at}``."""
    doc = _state_doc(BINDING_PREFIX + plugin)
    if not doc or _now(now) - float(doc.get("at") or 0.0) > BINDING_KEEP_S:
        return None
    return {"team": str(doc.get("team") or ""), "pct": int(doc.get("pct") or 0), "at": float(doc.get("at") or 0.0)}


def binding_put(plugin: str, team: str | None, pct: int = 0) -> None:
    """The switch's progress (``team`` None: it is over)."""
    if team is None:
        _state_put(BINDING_PREFIX + plugin, None)
        return
    _state_put(BINDING_PREFIX + plugin, {"team": team, "pct": max(0, min(100, int(pct))), "at": time.time()})


def unit_tries(account: str, space: str, plugin: str) -> dict[str, dict[str, Any]]:
    """Folder units (``<folder>/<unit>``) whose changeset failed as a whole lately:
    ``{unit: {attempts, next_try, error}}`` (each waits until its ``next_try``)."""
    doc = _state_doc(f"{UNIT_TRIES_PREFIX}{account}:{space}:{plugin}")
    return {str(u): {"attempts": int(t.get("attempts") or 0), "next_try": float(t.get("next_try") or 0.0),
                     "error": str(t.get("error") or "")}
            for u, t in doc.items() if isinstance(t, dict)}


def unit_try_note(account: str, space: str, plugin: str, units: Iterable[str], error: str, *,
                  block_after: int) -> dict[str, Any]:
    """These folder units' (``<folder>/<unit>``) changeset failed as a whole: they count one
    try together (as many as the most-tried of them) and wait ``min(2**attempts * 30 s,
    1 h)``. Returns ``{attempts, next_try, blocked}``; ``blocked`` once ``block_after``
    tries failed, when the record of them goes (the caller marks the units)."""
    key = f"{UNIT_TRIES_PREFIX}{account}:{space}:{plugin}"
    doc = {u: t for u, t in _state_doc(key).items() if isinstance(t, dict)}
    units = list(dict.fromkeys(u for u in units if u))
    attempts = max((int((doc.get(u) or {}).get("attempts") or 0) for u in units), default=0) + 1
    next_try = time.time() + min(2 ** attempts * 30.0, BLOCKED_MAX_WAIT_S)
    blocked = attempts >= block_after
    for u in units:
        if blocked:
            doc.pop(u, None)
        else:
            doc[u] = {"attempts": attempts, "next_try": next_try, "error": str(error or "")[:500]}
    _state_put(key, doc or None)
    return {"attempts": attempts, "next_try": next_try, "blocked": blocked}


def unit_tries_clear(account: str, space: str, plugin: str, units: Iterable[str]) -> None:
    """These folder units went out (or were let go): no more waiting for them."""
    key = f"{UNIT_TRIES_PREFIX}{account}:{space}:{plugin}"
    doc = _state_doc(key)
    gone = [u for u in units if u in doc]
    if gone:
        for u in gone:
            doc.pop(u, None)
        _state_put(key, doc or None)


# --------------------------------------------------------------------------- housekeeping


def prune(now: float | None = None) -> dict[str, int]:
    """Drop what has served its purpose: activity older than 2 minutes, committed
    changesets and closed groups older than a day (a group some row still names stays),
    answered lock requests older than a minute."""
    now = _now(now)
    conn = db.connect()
    out: dict[str, int] = {}
    with db.write_txn(conn):
        out["activity"] = conn.execute("DELETE FROM space_activity WHERE at<?", (now - ACTIVITY_KEEP_S,)).rowcount
        out["changesets"] = conn.execute(
            "DELETE FROM space_changesets WHERE state NOT IN ('pending', 'pushing') AND updated<?",
            (now - CHANGESET_KEEP_S,),
        ).rowcount
        out["groups"] = conn.execute(
            "DELETE FROM space_groups WHERE open=0 AND closed<? AND NOT EXISTS "
            "(SELECT 1 FROM plugin_data d WHERE d.group_id=space_groups.group_id)",
            (now - GROUP_KEEP_S,),
        ).rowcount
        out["requests"] = conn.execute(
            "DELETE FROM space_lock_requests WHERE state<>'pending' AND created<?", (now - REQUEST_KEEP_S,)
        ).rowcount
    return out


def forget_space(account: str, space: str) -> None:
    """Everything this module keeps of a space on this PC (its copy is being purged)."""
    conn = db.connect()
    with db.write_txn(conn):
        for table in ("space_heads", "space_changesets", "space_conflicts", "space_incoming_blocked", "space_locks",
                      "space_presence", "space_activity", "space_lock_requests", "plugin_data_base", "space_units",
                      "space_lost_choice"):
            conn.execute(f"DELETE FROM {table} WHERE account_id=? AND space_id=?", (account, space))
        _queues_drop(conn, account, space)


def _queues_drop(conn: Any, account: str, space: str) -> None:
    """Every plugin's changesets waiting behind "Update now" in the space, and its folder
    units' tries."""
    for head in (INCOMING_PREFIX, UNIT_TRIES_PREFIX):
        prefix = f"{head}{account}:{space}:"
        conn.execute("DELETE FROM workspace_state WHERE key>=? AND key<?",
                     (prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)))
