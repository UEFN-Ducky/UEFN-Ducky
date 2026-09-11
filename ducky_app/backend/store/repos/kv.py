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
