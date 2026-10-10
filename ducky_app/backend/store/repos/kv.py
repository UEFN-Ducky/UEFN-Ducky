"""Shared helpers for the key → JSON document tables (settings, workspace_state, cache_docs)."""

from __future__ import annotations

import json
import time
from typing import Any

from backend.store import db


def get_doc(table: str, key: str) -> Any | None:
    row = db.connect().execute(f"SELECT value FROM {table} WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def set_doc(table: str, key: str, value: Any) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            f"INSERT INTO {table}(key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )


def delete_doc(table: str, key: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(f"DELETE FROM {table} WHERE key=?", (key,))


def prune_docs(table: str, prefix: str, *, keep: int) -> int:
    """Delete documents whose key starts with *prefix* (non-empty), except the
    *keep* with the highest keys. Returns how many were deleted."""
    upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)  # key range = the prefix, on the key index
    conn = db.connect()
    total = conn.execute(f"SELECT count(*) FROM {table} WHERE key >= ? AND key < ?", (prefix, upper)).fetchone()[0]
    if total <= keep:
        return 0
    with db.write_txn(conn):
        cur = conn.execute(
            f"DELETE FROM {table} WHERE key >= ? AND key < ? AND key NOT IN "
            f"(SELECT key FROM {table} WHERE key >= ? AND key < ? ORDER BY key DESC LIMIT ?)",
            (prefix, upper, prefix, upper, keep),
        )
    return cur.rowcount


def list_docs(table: str, prefix: str = "") -> dict[str, Any]:
    conn = db.connect()
    rows = conn.execute(
        f"SELECT key, value FROM {table} WHERE key LIKE ? ORDER BY key", (prefix + "%",)
    ).fetchall()
    out: dict[str, Any] = {}
    for key, value in rows:
        try:
            out[str(key)] = json.loads(value)
        except ValueError:
            continue
    return out


def meta_get(key: str) -> str | None:
    row = db.connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def meta_set(key: str, value: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO meta(key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (key, value, time.time()),
        )
