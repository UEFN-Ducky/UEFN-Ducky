"""Host data service tables (migration 0009): ``plugin_data`` docs + asset metadata,
``scope_sync`` per-space sync state, ``plugin_scopes`` where each plugin's data lives.
Each row also says why a shared change hasn't gone out yet (migration 0020).

Every call names its ``(account, scope)``; nothing here reads across accounts.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from backend.store import db
from backend.store.repos import folder_files

_COLS = ("plugin_id, kind, key, value, size, sha256, rev, dirty, deleted, sensitive, updated, sync_state, sync_error, "
         "attempts, next_try")
_WHERE = "account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=?"
# Why a dirty row isn't going out: '' = queued as usual. Refused and blocked rows stay
# dirty but leave the queue until a retry, a new write or a plan change re-queues them.
SYNC_STATES = frozenset({"", "refused", "blocked", "conflict", "locked_out"})
BLOCK_AFTER = 5  # local push failures in a row before a row is blocked
REFUSED_REASON = "The server didn't accept this change."
_FRESH = "sync_state='', sync_error='', attempts=0, next_try=0"


def _row(r: Any) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def _replace(temp_path: str | os.PathLike[str], target_path: str | os.PathLike[str]) -> None:
    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_path, target_path)


# --------------------------------------------------------------------------- docs + assets


def get(account: str, scope: str, plugin: str, kind: str, key: str) -> dict[str, Any] | None:
    r = db.connect().execute(
        f"SELECT {_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=?",
        (account, scope, plugin, kind, key),
    ).fetchone()
    return _row(r) if r else None


def put(
    account: str,
    scope: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    value: str | None,
    size: int,
    sha256: str,
    dirty: bool,
    rev: int | None = None,
    sensitive: bool = False,
    group_id: str = "",
) -> None:
    """Upsert a live row. ``rev=None`` keeps the stored rev (a local write). ``group_id``:
    the write group (one tool call or panel call) the write was made in, '' for none."""
    conn = db.connect()
    with db.write_txn(conn):
        upsert(conn, account, scope, plugin, kind, key, value=value, size=size, sha256=sha256, dirty=dirty, rev=rev,
               sensitive=sensitive, group_id=group_id)


def upsert(
    conn: Any,
    account: str,
    scope: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    value: str | None,
    size: int,
    sha256: str,
    dirty: bool,
    rev: int | None = None,
    sensitive: bool = False,
    group_id: str = "",
) -> None:
    """:func:`put` inside a transaction the caller holds (writes that must land together).
    Every write starts the row's sync afresh: a new edit or a pulled copy is never refused
    or blocked, and it goes out with the write group of its newest write."""
    conn.execute(
        "INSERT INTO plugin_data(account_id, scope_id, plugin_id, kind, key, value, size, sha256, rev, dirty, "
        "deleted, sensitive, updated, group_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?) "
        "ON CONFLICT(account_id, scope_id, plugin_id, kind, key) DO UPDATE SET value=excluded.value, "
        "size=excluded.size, sha256=excluded.sha256, rev=COALESCE(?, plugin_data.rev), dirty=excluded.dirty, "
        f"deleted=0, sensitive=excluded.sensitive, updated=excluded.updated, group_id=excluded.group_id, {_FRESH}",
        (account, scope, plugin, kind, key, value, size, sha256, rev or 0, 1 if dirty else 0,
         1 if sensitive else 0, time.time(), group_id, rev),
    )


def put_file_row(
    account: str,
    scope: str,
    plugin: str,
    key: str,
    *,
    temp_path: str | os.PathLike[str],
    target_path: str | os.PathLike[str],
    size: int,
    sha256: str,
    dirty: bool,
    group_id: str = "",
) -> None:
    """A local file write: ``temp_path`` (the sealed bytes, in the target's own folder)
    replaces ``target_path`` inside the row's transaction, so the row and the file never
    disagree. The temp file is gone afterwards either way."""
    conn = db.connect()
    try:
        with db.write_txn(conn):
            upsert(conn, account, scope, plugin, "asset", key, value=None, size=size, sha256=sha256, dirty=dirty,
                   group_id=group_id)
            _replace(temp_path, target_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def remove(
    account: str,
    scope: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    tombstone: bool,
    target_path: str | os.PathLike[str] | None = None,
    group_id: str = "",
) -> None:
    """Local delete: a tombstone waiting to push (it keeps the deleted content's sha256),
    or the row just goes. A file's ``target_path`` goes in the same transaction, after
    the row: a file held open raises and keeps both, and a pull can't land in between."""
    conn = db.connect()
    args = (account, scope, plugin, kind, key)
    with db.write_txn(conn):
        if tombstone:
            conn.execute(
                f"UPDATE plugin_data SET value=NULL, size=0, deleted=1, dirty=1, updated=?, group_id=?, {_FRESH} "
                f"WHERE {_WHERE}",
                (time.time(), group_id, *args),
            )
        else:
            conn.execute(f"DELETE FROM plugin_data WHERE {_WHERE}", args)
        if target_path is not None:
            Path(target_path).unlink(missing_ok=True)


def written_since(account: str, scope: str, plugin: str, since: float) -> list[dict[str, Any]]:
    """This PC's own changes of ``plugin`` written after ``since`` (dirty rows, deletes
    included; never sensitive ones), oldest first: what a live group hears at once."""
    out = db.connect().execute(
        f"SELECT {_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=? AND dirty=1 "
        "AND sensitive=0 AND updated>? ORDER BY updated",
        (account, scope, plugin, since),
    ).fetchall()
    return [_row(r) for r in out]


def delete_row(conn: Any, account: str, scope: str, plugin: str, kind: str, key: str) -> None:
    """A row goes (no tombstone), inside a transaction the caller holds."""
    conn.execute(f"DELETE FROM plugin_data WHERE {_WHERE}", (account, scope, plugin, kind, key))


def plugin_ids(account: str, scope: str) -> list[str]:
    """Plugin ids with live, non-sensitive rows in one scope."""
    out = db.connect().execute(
        "SELECT DISTINCT plugin_id FROM plugin_data WHERE account_id=? AND scope_id=? AND deleted=0 AND sensitive=0 "
        "ORDER BY plugin_id",
        (account, scope),
    ).fetchall()
    return [str(r[0]) for r in out]


def scope_ids(account: str) -> list[str]:
    """Scopes (Local and shared spaces) holding live, non-sensitive rows of the account."""
    out = db.connect().execute(
        "SELECT DISTINCT scope_id FROM plugin_data WHERE account_id=? AND deleted=0 AND sensitive=0 ORDER BY scope_id",
        (account,),
    ).fetchall()
    return [str(r[0]) for r in out]


def has_rows(account: str, plugin: str, scope: str | None = None) -> bool:
    """Any row of ``plugin`` (tombstones and sensitive ones too) in ``scope``, or in any
    scope of the account."""
    sql, args = "SELECT 1 FROM plugin_data WHERE account_id=? AND plugin_id=?", [account, plugin]
    if scope is not None:
        sql, args = sql + " AND scope_id=?", [*args, scope]
    return db.connect().execute(sql + " LIMIT 1", args).fetchone() is not None


def rows(account: str, scope: str, plugin: str, kind: str, prefix: str = "") -> list[dict[str, Any]]:
    """Live rows whose key starts with exactly ``prefix`` (``_`` is no wildcard). Keys are
    ASCII, so ``prefix + chr(127)`` bounds the range."""
    out = db.connect().execute(
        f"SELECT {_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=? AND kind=? "
        "AND deleted=0 AND key>=? AND key<? ORDER BY key",
        (account, scope, plugin, kind, prefix, prefix + "\x7f"),
    ).fetchall()
    return [_row(r) for r in out]


def dirty(account: str, scope: str, limit: int) -> list[dict[str, Any]]:
    """Changes to push: fewest failed attempts first, then oldest, so a row that keeps
    failing never holds up newer ones. Sensitive rows never leave the PC; refused and
    blocked rows wait for a retry, and a row backing off waits for its ``next_try``."""
    out = db.connect().execute(
        f"SELECT {_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND dirty=1 AND sensitive=0 "
        "AND sync_state NOT IN ('refused', 'blocked') AND next_try<=? ORDER BY attempts, updated LIMIT ?",
        (account, scope, time.time(), limit),
    ).fetchall()
    return [_row(r) for r in out]


def count_dirty(account: str, scope: str, plugin: str | None = None) -> int:
    """Changes still to send (rows backing off included; refused and blocked rows are in
    :func:`state_counts`). ``plugin``: that plugin's only."""
    sql = ("SELECT COUNT(*) FROM plugin_data WHERE account_id=? AND scope_id=? AND dirty=1 AND sensitive=0 "
           "AND sync_state NOT IN ('refused', 'blocked')")
    args = [account, scope]
    if plugin is not None:
        sql, args = sql + " AND plugin_id=?", [*args, plugin]
    return int(db.connect().execute(sql, args).fetchone()[0])


def unsent_spaces(account: str) -> dict[str, float]:
    """``{scope: newest write}`` of the account's changes still to send, per scope (the
    background sync's one look per change; refused and blocked rows wait for a retry)."""
    out = db.connect().execute(
        "SELECT scope_id, MAX(updated) FROM plugin_data WHERE account_id=? AND dirty=1 AND sensitive=0 "
        "AND sync_state NOT IN ('refused', 'blocked') GROUP BY scope_id",
        (account,),
    ).fetchall()
    return {str(r[0]): float(r[1] or 0.0) for r in out}


def mark_pushed(
    account: str, scope: str, plugin: str, kind: str, key: str, *, rev: int, sha256: str, pushed_delete: bool = False
) -> None:
    """The server confirmed a push at ``rev``.

    A delete (``pushed_delete``, or ``sha256 == ""`` from providers older than the flag):
    the row goes while it is still a tombstone; a row re-created since keeps its change
    and takes ``rev`` as its base. A content push (``sha256`` of the pushed plaintext)
    clears ``dirty`` only while the row still holds that content: an edit made meanwhile,
    or a delete (tombstones keep the deleted sha), stays queued on the new base rev."""
    conn = db.connect()
    args = (account, scope, plugin, kind, key)
    with db.write_txn(conn):
        if pushed_delete or not sha256:
            if not conn.execute(f"DELETE FROM plugin_data WHERE {_WHERE} AND deleted=1", args).rowcount:
                conn.execute(f"UPDATE plugin_data SET rev=?, {_FRESH} WHERE {_WHERE}", (rev, *args))
            return
        conn.execute(
            f"UPDATE plugin_data SET rev=?, dirty=CASE WHEN deleted=0 AND sha256=? THEN 0 ELSE dirty END, {_FRESH} "
            f"WHERE {_WHERE}",
            (rev, sha256, *args),
        )


def set_rev(account: str, scope: str, plugin: str, kind: str, key: str, rev: int) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "UPDATE plugin_data SET rev=? WHERE account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=?",
            (rev, account, scope, plugin, kind, key),
        )


def mark_plugin_dirty(account: str, scope: str, plugin: str) -> None:
    """Queue every live doc of one plugin to push on the next sync round, refused and
    blocked ones too."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "UPDATE plugin_data SET dirty=1, attempts=0, next_try=0, "
            "sync_error=CASE WHEN sync_state IN ('refused', 'blocked') THEN '' ELSE sync_error END, "
            "sync_state=CASE WHEN sync_state IN ('refused', 'blocked') THEN '' ELSE sync_state END "
            "WHERE account_id=? AND scope_id=? AND plugin_id=? AND deleted=0",
            (account, scope, plugin),
        )


def mark_dirty(account: str, scope: str, plugin: str, kind: str, key: str) -> None:
    """Queue one live item to push again as it is (the server's copy needs replacing)."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "UPDATE plugin_data SET dirty=1 WHERE account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=? "
            "AND deleted=0",
            (account, scope, plugin, kind, key),
        )


def set_state(
    account: str,
    scope: str,
    plugin: str,
    kind: str,
    key: str,
    state: str,
    reason: str = "",
    expect_sha: str | None = None,
) -> bool:
    """Record why a row isn't syncing (``refused``, ``blocked``, ``conflict``,
    ``locked_out``); ``''`` puts it back in the queue with fresh attempts. ``dirty`` is
    kept. ``expect_sha``: only while the row still holds what was pushed (``""``: the push
    was a delete), so a newer edit is never marked. False when no row matched."""
    if state not in SYNC_STATES:
        raise ValueError(f"unknown sync state: {state!r}")
    where, args = _WHERE, [account, scope, plugin, kind, key]
    if expect_sha:
        where, args = where + " AND sha256=? AND deleted=0", [*args, expect_sha]
    elif expect_sha is not None:
        where += " AND deleted=1"
    fresh = "" if state else ", attempts=0, next_try=0"
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute(f"UPDATE plugin_data SET sync_state=?, sync_error=?{fresh} WHERE {where}",
                           (state, str(reason or "")[:500], *args))
    return cur.rowcount > 0


def clear_dirty(account: str, scope: str, plugin: str, kind: str, key: str) -> None:
    """What providers older than :func:`set_state` call for a push the server refused: the
    row is kept as refused (still dirty, out of the queue) instead of looking synced."""
    set_state(account, scope, plugin, kind, key, "refused", REFUSED_REASON)


def note_attempt(account: str, scope: str, plugin: str, kind: str, key: str, error: str) -> dict[str, Any] | None:
    """A push of this row failed on this PC (file missing, unreadable, can't be sealed). It
    waits ``min(2**attempts * 30 s, 1 h)`` and is ``blocked`` after :data:`BLOCK_AFTER`
    failures. Returns ``{attempts, next_try, state}``; None when there is no such row."""
    conn = db.connect()
    args = (account, scope, plugin, kind, key)
    with db.write_txn(conn):
        r = conn.execute(f"SELECT attempts, sync_state FROM plugin_data WHERE {_WHERE}", args).fetchone()
        if r is None:
            return None
        attempts = int(r[0]) + 1
        next_try = time.time() + min(2 ** attempts * 30, 3600)
        state = "blocked" if attempts >= BLOCK_AFTER else str(r[1])
        conn.execute(
            f"UPDATE plugin_data SET attempts=?, next_try=?, sync_state=?, sync_error=? WHERE {_WHERE}",
            (attempts, next_try, state, str(error or "")[:500], *args),
        )
    return {"attempts": attempts, "next_try": next_try, "state": state}


def note_attempts(account: str, scope: str, plugin: str, items: Iterable[tuple[str, str]],
                  error: str) -> dict[str, Any] | None:
    """A changeset of these rows failed as a whole (the server took none of it and said
    why). They count one try together, as many as the most-tried of them, so they wait
    alike (:func:`note_attempt`'s backoff), come back together and are ``blocked`` with
    the reason together after :data:`BLOCK_AFTER`. Returns ``{attempts, next_try, state}``;
    None when none of them is here any more."""
    conn = db.connect()
    with db.write_txn(conn):
        found = []
        for kind, key in dict.fromkeys(items):
            r = conn.execute(f"SELECT attempts, sync_state FROM plugin_data WHERE {_WHERE}",
                             (account, scope, plugin, kind, key)).fetchone()
            if r is not None:
                found.append((kind, key, r))
        if not found:
            return None
        attempts = max(int(r[0]) for *_, r in found) + 1
        next_try = time.time() + min(2 ** attempts * 30, 3600)
        blocked = attempts >= BLOCK_AFTER
        for kind, key, r in found:
            conn.execute(
                f"UPDATE plugin_data SET attempts=?, next_try=?, sync_state=?, sync_error=? WHERE {_WHERE}",
                (attempts, next_try, "blocked" if blocked else str(r[1]), str(error or "")[:500],
                 account, scope, plugin, kind, key),
            )
    return {"attempts": attempts, "next_try": next_try, "state": "blocked" if blocked else ""}


def requeue_refused(account: str, scope: str) -> int:
    """Refused rows of a scope go back in the queue (the plan or the space's usage
    changed), folder files plugins keep too. Returns how many."""
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute(
            f"UPDATE plugin_data SET {_FRESH} WHERE account_id=? AND scope_id=? AND sync_state='refused'",
            (account, scope),
        )
        return cur.rowcount + folder_files.requeue_refused(conn, account, scope)


def settle_reset(account: str, scope: str, *, keep: bool) -> int:
    """The person's choice after the space's data was reset on the server. It is for this
    PC's whole copy of the space (every plugin's, workflows too), as the provider waits
    for the whole copy: ``keep`` (Upload my copy) queues every live row again as new (base
    rev 0) and drops tombstones (nothing is left there to delete); otherwise (Clear my
    copy) every row goes, and the space's data comes down instead. The space is writable
    again and pulls from the start, all in one transaction: on the older sync its cursor
    goes back to 0, on the new one every plugin's head does, and what this PC kept of the
    old versions goes (changesets, merge bases, unit seqs, items set aside, conflicts; a
    conflict's losing version of mine is folded into its row first on Upload my copy).
    Folder files plugins keep go up again as new too, or their rows go
    (:func:`folder_files.reset_rows`). Returns how many rows and files were queued, or
    removed."""
    conn = db.connect()
    args = (account, scope)
    with db.write_txn(conn):
        for table in ("space_changesets", "space_conflicts", "space_incoming_blocked", "plugin_data_base",
                      "space_units"):
            conn.execute(f"DELETE FROM {table} WHERE account_id=? AND space_id=?", args)
        conn.execute("UPDATE space_heads SET seq=0, remote_seq=0, moves=0, attempts=0, next_try=0 "
                     "WHERE account_id=? AND space_id=?", args)
        if keep:
            cur = conn.execute(
                f"UPDATE plugin_data SET rev=0, dirty=1, {_FRESH} WHERE account_id=? AND scope_id=? AND deleted=0 "
                "AND sensitive=0",
                args,
            )
            conn.execute("DELETE FROM plugin_data WHERE account_id=? AND scope_id=? AND deleted=1", args)
        else:
            cur = conn.execute("DELETE FROM plugin_data WHERE account_id=? AND scope_id=?", args)
        count = cur.rowcount + folder_files.reset_rows(conn, account, scope, keep=keep)  # folders plugins keep
        conn.execute(
            "UPDATE scope_sync SET state='ok', error='', cursor_rev=0, rewind_rev=0, rewind_at=0 "
            "WHERE account_id=? AND space_id=?",
            args,
        )
    return count


def state_counts(account: str, scope: str, plugin: str | None = None) -> dict[str, int]:
    """Rows that aren't syncing, per state: ``{refused, blocked, conflict, locked_out}``."""
    sql = "SELECT sync_state, COUNT(*) FROM plugin_data WHERE account_id=? AND scope_id=? AND sync_state<>''"
    args = [account, scope]
    if plugin is not None:
        sql, args = sql + " AND plugin_id=?", [*args, plugin]
    out = {state: 0 for state in SYNC_STATES if state}
    for state, count in db.connect().execute(sql + " GROUP BY sync_state", args).fetchall():
        out[str(state)] = int(count)
    return out


def state_items(account: str, scope: str, plugin: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The rows behind :func:`state_counts`, newest first: plugin_id, kind, key, deleted,
    sync_state, sync_error (the reason), attempts, next_try."""
    sql = ("SELECT plugin_id, kind, key, deleted, sync_state, sync_error, attempts, next_try FROM plugin_data "
           "WHERE account_id=? AND scope_id=? AND sync_state<>''")
    args: list[Any] = [account, scope]
    if plugin is not None:
        sql, args = sql + " AND plugin_id=?", [*args, plugin]
    return [_row(r) for r in db.connect().execute(sql + " ORDER BY updated DESC LIMIT ?", [*args, limit]).fetchall()]


def pending_items(account: str, scope: str, plugin: str, limit: int = 200) -> list[dict[str, Any]]:
    """One plugin's changes not sent yet (dirty, never sensitive), oldest first, without
    their content: kind, key, size, deleted, sync_state, sync_error, attempts, next_try."""
    sql = ("SELECT kind, key, size, deleted, sync_state, sync_error, attempts, next_try FROM plugin_data "
           "WHERE account_id=? AND scope_id=? AND plugin_id=? AND dirty=1 AND sensitive=0 ORDER BY updated LIMIT ?")
    return [_row(r) for r in db.connect().execute(sql, (account, scope, plugin, limit)).fetchall()]


def _unchanged(row: dict[str, Any] | None, expect: dict[str, Any] | None) -> bool:
    """The row is still what the caller read before its download: absent both times, or
    clean with the same content and deleted flag (no local write landed meanwhile)."""
    if row is None or expect is None:
        return row is None and expect is None
    return (
        not int(row["dirty"])
        and row["sha256"] == str(expect.get("sha256") or "")
        and bool(int(row["deleted"])) == bool(int(expect.get("deleted") or 0))
    )


def _current(conn: Any, args: tuple[str, ...]) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT {_COLS} FROM plugin_data WHERE {_WHERE}", args).fetchone()
    return _row(r) if r else None


def apply_remote(
    account: str,
    scope: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    value: str | None,
    size: int,
    sha256: str,
    rev: int,
    expect: dict[str, Any] | None,
    temp_path: str | os.PathLike[str] | None = None,
    target_path: str | os.PathLike[str] | None = None,
) -> bool:
    """Land a pulled copy as a clean row at ``rev``, unless a local write won the race.
    ``expect`` is the row read before the download (None: there was none). For a file,
    ``temp_path`` (in the target's own folder) replaces ``target_path`` inside the same
    transaction. False: nothing changed, the local write stands (its push or the stale
    path settles it). The temp file is gone afterwards either way."""
    conn = db.connect()
    try:
        with db.write_txn(conn):
            if not _unchanged(_current(conn, (account, scope, plugin, kind, key)), expect):
                return False
            upsert(conn, account, scope, plugin, kind, key, value=value, size=size, sha256=sha256, dirty=False, rev=rev)
            if temp_path is not None and target_path is not None:
                _replace(temp_path, target_path)
        return True
    finally:
        if temp_path is not None:
            Path(temp_path).unlink(missing_ok=True)


def remove_remote(
    account: str,
    scope: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    expect: dict[str, Any] | None,
    target_path: str | os.PathLike[str] | None = None,
) -> bool:
    """A pulled delete, on the terms of :func:`apply_remote`. The row and ``target_path``
    go in one transaction (a file held open raises and keeps the row)."""
    conn = db.connect()
    args = (account, scope, plugin, kind, key)
    with db.write_txn(conn):
        row = _current(conn, args)
        if not _unchanged(row, expect):
            return False
        if row is not None:
            conn.execute(f"DELETE FROM plugin_data WHERE {_WHERE}", args)
            if target_path is not None:
                Path(target_path).unlink(missing_ok=True)
    return True


def drop_held(
    account: str,
    scope: str,
    plugin: str,
    kind: str,
    key: str,
    *,
    expect: dict[str, Any],
    target_path: str | os.PathLike[str] | None = None,
) -> bool:
    """Drop my change: the row ``expect`` (read just before) goes, with ``target_path``,
    in one transaction, and only while it is still that change held back (dirty, refused
    or blocked, same content, same deleted flag). A newer edit, or a change whose push is
    under way, is never dropped. When the team has the item (rev > 0) it comes back: on
    the older sync the space's cursor goes back to its rev (the pull cursor is inclusive)
    and stays at most there until a round that started after the drop writes it, so a
    round already running can't move it past again; on the new sync (the plugin's head is
    past it) the item is set aside to be fetched at its current version on the next
    round. False when the row changed meanwhile."""
    conn = db.connect()
    args = (account, scope, plugin, kind, key)
    rev = int(expect.get("rev") or 0)
    with db.write_txn(conn):
        cur = conn.execute(
            f"DELETE FROM plugin_data WHERE {_WHERE} AND dirty=1 AND sync_state IN ('refused', 'blocked') "
            "AND sha256=? AND deleted=?",
            (*args, str(expect.get("sha256") or ""), 1 if int(expect.get("deleted") or 0) else 0),
        )
        if not cur.rowcount:
            return False
        engine = conn.execute("SELECT engine FROM space_heads WHERE account_id=? AND space_id=? AND plugin_id=?",
                              (account, scope, plugin)).fetchone()
        if rev > 0 and engine is not None and engine[0] == "v2":
            conn.execute(
                "INSERT INTO space_incoming_blocked(account_id, space_id, plugin_id, kind, key, unit, seq, error, "
                "attempts, next_try) VALUES (?, ?, ?, ?, ?, ?, ?, 'Getting the team''s version.', 0, 0) "
                "ON CONFLICT(account_id, space_id, plugin_id, kind, key) "
                "DO UPDATE SET seq=MAX(space_incoming_blocked.seq, excluded.seq), next_try=0",
                (*args, f"{kind}:{key}", rev),
            )
        elif rev > 0:
            conn.execute(
                "UPDATE scope_sync SET cursor_rev=MIN(cursor_rev, ?), "
                "rewind_rev=CASE WHEN rewind_at>0 THEN MIN(rewind_rev, ?) ELSE ? END, rewind_at=? "
                "WHERE account_id=? AND space_id=?",
                (rev, rev, rev, time.time(), account, scope),
            )
        if target_path is not None:
            Path(target_path).unlink(missing_ok=True)
    return True


def delete_plugin(account: str, plugin: str) -> list[str]:
    """Erase ``plugin``'s rows in every scope of the account, and its link. The spaces'
    cursors stay: their next rounds must not bring the erased copy back (a pull stores
    every plugin's items). Linking it again pulls from the start
    (:func:`backend.uefn_plugins.scopes.link_plugin`). Returns the scopes it had rows in."""
    conn = db.connect()
    with db.write_txn(conn):
        held = [str(r[0]) for r in conn.execute(
            "SELECT DISTINCT scope_id FROM plugin_data WHERE account_id=? AND plugin_id=?", (account, plugin)
        ).fetchall()]
        conn.execute("DELETE FROM plugin_data WHERE account_id=? AND plugin_id=?", (account, plugin))
        conn.execute("DELETE FROM plugin_scopes WHERE account_id=? AND plugin_id=?", (account, plugin))
    return held


def scope_plugin_ids(account: str, scope: str) -> list[str]:
    """Every plugin id with rows in one scope, deleted and sensitive ones too."""
    out = db.connect().execute(
        "SELECT DISTINCT plugin_id FROM plugin_data WHERE account_id=? AND scope_id=? ORDER BY plugin_id",
        (account, scope),
    ).fetchall()
    return [str(r[0]) for r in out]


def delete_scope_plugin(account: str, scope: str, plugin: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=?", (account, scope, plugin))


def delete_scope(account: str, scope: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM plugin_data WHERE account_id=? AND scope_id=?", (account, scope))
        conn.execute("DELETE FROM scope_sync WHERE account_id=? AND space_id=?", (account, scope))
        conn.execute("DELETE FROM plugin_scopes WHERE account_id=? AND scope_id=?", (account, scope))


# --------------------------------------------------------------------------- sync state


def sync_get(account: str, space: str) -> dict[str, Any]:
    r = db.connect().execute(
        "SELECT label, members, cursor_rev, state, error, usage, synced_at, called_at FROM scope_sync "
        "WHERE account_id=? AND space_id=?",
        (account, space),
    ).fetchone()
    if not r:
        return {"label": "", "members": 0, "cursor_rev": 0, "state": "ok", "error": "", "usage": {},
                "synced_at": 0.0, "called_at": 0.0}
    out = _row(r)
    try:
        out["usage"] = json.loads(out["usage"] or "{}")
    except ValueError:
        out["usage"] = {}
    return out


_SYNC_FIELDS: dict[str, Any] = {
    "label": str, "members": int, "cursor_rev": int, "state": str, "error": str,
    "usage": lambda v: json.dumps(v or {}), "synced_at": float, "called_at": float,
}
# A round reads the cursor a moment before it stamps ``called_at``: one stamped this soon
# after a drop (:func:`drop_held`) may still pull from the cursor before it.
REWIND_GRACE_S = 5.0
# While a drop's rewind holds, a cursor write goes no further than the dropped item's rev;
# the first write by a round that started after the drop ends the hold.
_CURSOR_SET = (
    "cursor_rev=CASE WHEN scope_sync.rewind_at>0 AND scope_sync.called_at<=scope_sync.rewind_at+{grace} "
    "THEN MIN(excluded.cursor_rev, scope_sync.rewind_rev) ELSE excluded.cursor_rev END, "
    "rewind_at=CASE WHEN scope_sync.called_at>scope_sync.rewind_at+{grace} THEN 0 ELSE scope_sync.rewind_at END"
).format(grace=REWIND_GRACE_S)


def sync_put(account: str, space: str, **fields: Any) -> None:
    """Set ``fields`` on the space's sync row in one statement, so two writers (a round and
    a picker refresh, in any process) never undo each other's columns. Other names are
    ignored. A ``cursor_rev`` honours a dropped change's rewind (:func:`drop_held`). A
    space back to ``ok`` forgets what the person chose for unsent changes when access
    went: a later loss asks again."""
    cols = [name for name in fields if name in _SYNC_FIELDS]
    if not cols:
        return
    values = [_SYNC_FIELDS[name](fields[name]) for name in cols]
    sets = [_CURSOR_SET if c == "cursor_rev" else f"{c}=excluded.{c}" for c in cols]
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            f"INSERT INTO scope_sync(account_id, space_id, {', '.join(cols)}) VALUES (?, ?{', ?' * len(cols)}) "
            f"ON CONFLICT(account_id, space_id) DO UPDATE SET {', '.join(sets)}",
            (account, space, *values),
        )
        if fields.get("state") == "ok":
            conn.execute("DELETE FROM space_lost_choice WHERE account_id=? AND space_id=?", (account, space))


# --------------------------------------------------------------------------- plugin links


def link_get(account: str, plugin: str) -> str:
    """Where this plugin's data lives for the account: ``personal`` (Local) or a space id."""
    r = db.connect().execute(
        "SELECT scope_id FROM plugin_scopes WHERE account_id=? AND plugin_id=?", (account, plugin)
    ).fetchone()
    return str(r[0]) if r else "personal"


def link_set(account: str, plugin: str, scope: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        if scope == "personal":
            conn.execute("DELETE FROM plugin_scopes WHERE account_id=? AND plugin_id=?", (account, plugin))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO plugin_scopes(account_id, plugin_id, scope_id, updated) VALUES (?, ?, ?, ?)",
                (account, plugin, scope, time.time()),
            )


def links(account: str) -> dict[str, str]:
    """Every plugin of the account whose data lives in a shared space: ``{plugin_id: space_id}``."""
    rows = db.connect().execute(
        "SELECT plugin_id, scope_id FROM plugin_scopes WHERE account_id=?", (account,)
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def totals(account: str, scope: str, plugin: str) -> dict[str, int]:
    """Live docs and files of one plugin in one scope: counts and plaintext bytes."""
    rows = db.connect().execute(
        "SELECT kind, COUNT(*), COALESCE(SUM(size), 0) FROM plugin_data WHERE account_id=? AND scope_id=? "
        "AND plugin_id=? AND deleted=0 AND sensitive=0 GROUP BY kind",
        (account, scope, plugin),
    ).fetchall()
    out = {"docs": 0, "files": 0, "docsBytes": 0, "filesBytes": 0}
    for kind, count, size in rows:
        name = "docs" if kind == "doc" else "files"
        out[name], out[name + "Bytes"] = int(count), int(size)
    return out
