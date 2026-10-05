"""Host data service tables (migration 0009): ``plugin_data`` docs + asset metadata,
``scope_sync`` per-team sync state, ``plugin_scopes`` where each plugin's data lives.

Every call names its ``(account, scope)``; nothing here reads across accounts.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.store import db

_COLS = "plugin_id, kind, key, value, size, sha256, rev, dirty, deleted, sensitive, updated"


def _row(r: Any) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


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
) -> None:
    """Upsert a live row. ``rev=None`` keeps the stored rev (a local write)."""
    conn = db.connect()
    with db.write_txn(conn):
        upsert(conn, account, scope, plugin, kind, key, value=value, size=size, sha256=sha256, dirty=dirty, rev=rev,
               sensitive=sensitive)


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
) -> None:
    """:func:`put` inside a transaction the caller holds (writes that must land together)."""
    conn.execute(
        "INSERT INTO plugin_data(account_id, scope_id, plugin_id, kind, key, value, size, sha256, rev, dirty, "
        "deleted, sensitive, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
        "ON CONFLICT(account_id, scope_id, plugin_id, kind, key) DO UPDATE SET value=excluded.value, "
        "size=excluded.size, sha256=excluded.sha256, rev=COALESCE(?, plugin_data.rev), dirty=excluded.dirty, "
        "deleted=0, sensitive=excluded.sensitive, updated=excluded.updated",
        (account, scope, plugin, kind, key, value, size, sha256, rev or 0, 1 if dirty else 0,
         1 if sensitive else 0, time.time(), rev),
    )


def remove(account: str, scope: str, plugin: str, kind: str, key: str, *, tombstone: bool) -> None:
    """Local delete: a synced row becomes a tombstone waiting to push; else it goes."""
    conn = db.connect()
    args = (account, scope, plugin, kind, key)
    where = "account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=?"
    with db.write_txn(conn):
        if tombstone:
            conn.execute(
                f"UPDATE plugin_data SET value=NULL, size=0, sha256='', deleted=1, dirty=1, updated=? WHERE {where}",
                (time.time(), *args),
            )
        else:
            conn.execute(f"DELETE FROM plugin_data WHERE {where}", args)


def plugin_ids(account: str, scope: str) -> list[str]:
    """Plugin ids with live, non-sensitive rows in one scope."""
    out = db.connect().execute(
        "SELECT DISTINCT plugin_id FROM plugin_data WHERE account_id=? AND scope_id=? AND deleted=0 AND sensitive=0 "
        "ORDER BY plugin_id",
        (account, scope),
    ).fetchall()
    return [str(r[0]) for r in out]


def rows(account: str, scope: str, plugin: str, kind: str, prefix: str = "") -> list[dict[str, Any]]:
    out = db.connect().execute(
        f"SELECT {_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND plugin_id=? AND kind=? "
        "AND deleted=0 AND key LIKE ? ORDER BY key",
        (account, scope, plugin, kind, prefix.replace("%", "") + "%"),
    ).fetchall()
    return [_row(r) for r in out]


def dirty(account: str, scope: str, limit: int) -> list[dict[str, Any]]:
    """Unpushed changes of a scope, oldest first. Sensitive rows never leave the PC."""
    out = db.connect().execute(
        f"SELECT {_COLS} FROM plugin_data WHERE account_id=? AND scope_id=? AND dirty=1 AND sensitive=0 "
        "ORDER BY updated LIMIT ?",
        (account, scope, limit),
    ).fetchall()
    return [_row(r) for r in out]


def count_dirty(account: str, scope: str) -> int:
    r = db.connect().execute(
        "SELECT COUNT(*) FROM plugin_data WHERE account_id=? AND scope_id=? AND dirty=1 AND sensitive=0",
        (account, scope),
    ).fetchone()
    return int(r[0])


def mark_pushed(account: str, scope: str, plugin: str, kind: str, key: str, *, rev: int, sha256: str) -> None:
    """The server holds ``sha256`` at ``rev``. A row edited again meanwhile keeps its
    dirty flag (it pushes next round with the new base rev); a pushed delete goes."""
    conn = db.connect()
    where = "account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=?"
    args = (account, scope, plugin, kind, key)
    with db.write_txn(conn):
        conn.execute(f"DELETE FROM plugin_data WHERE {where} AND deleted=1", args)
        conn.execute(
            f"UPDATE plugin_data SET rev=?, dirty=CASE WHEN sha256=? THEN 0 ELSE dirty END WHERE {where}",
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
    """Queue every live doc of one plugin to push on the next team round."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "UPDATE plugin_data SET dirty=1 WHERE account_id=? AND scope_id=? AND plugin_id=? AND deleted=0",
            (account, scope, plugin),
        )


def clear_dirty(account: str, scope: str, plugin: str, kind: str, key: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "UPDATE plugin_data SET dirty=0 WHERE account_id=? AND scope_id=? AND plugin_id=? AND kind=? AND key=?",
            (account, scope, plugin, kind, key),
        )


def delete_plugin(account: str, plugin: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM plugin_data WHERE account_id=? AND plugin_id=?", (account, plugin))


def delete_scope(account: str, scope: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM plugin_data WHERE account_id=? AND scope_id=?", (account, scope))
        conn.execute("DELETE FROM scope_sync WHERE account_id=? AND team_id=?", (account, scope))
        conn.execute("DELETE FROM plugin_scopes WHERE account_id=? AND scope_id=?", (account, scope))


# --------------------------------------------------------------------------- sync state


def sync_get(account: str, team: str) -> dict[str, Any]:
    r = db.connect().execute(
        "SELECT label, members, cursor_rev, state, error, usage, synced_at, called_at FROM scope_sync "
        "WHERE account_id=? AND team_id=?",
        (account, team),
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


def sync_put(account: str, team: str, **fields: Any) -> None:
    """Merge ``fields`` into the team's sync row."""
    row = sync_get(account, team)
    row.update(fields)
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO scope_sync(account_id, team_id, label, members, cursor_rev, state, error, usage, "
            "synced_at, called_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account, team, str(row["label"]), int(row["members"]), int(row["cursor_rev"]), str(row["state"]),
             str(row["error"]), json.dumps(row["usage"] or {}), float(row["synced_at"]), float(row["called_at"])),
        )


# --------------------------------------------------------------------------- plugin links


def link_get(account: str, plugin: str) -> str:
    """Where this plugin's data lives for the account: ``personal`` (Local) or a team id."""
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
    """Every plugin of the account whose data lives in a team: ``{plugin_id: team_id}``."""
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
