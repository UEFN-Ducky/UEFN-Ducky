"""``settings`` table: one row per :class:`PanelSettings` field, absence = default.

Reads are served from a per-process snapshot revalidated with
``PRAGMA data_version`` (one deserialisation per process until another
connection commits). Writes are per-key: :func:`save_fields` compares the new
values with the snapshot this process last saw and touches only the keys the
caller actually changed, so two processes editing different fields never
overwrite each other (the lost update ``PanelSettings.load()`` used to cause).
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from backend.store import db

_lock = threading.Lock()
_snapshot: dict[str, dict[str, Any]] = {}  # db path -> {field: value}
_snapshot_version: dict[str, int] = {}  # db path -> data_version the snapshot was read at
_history: dict[str, dict[int, dict[str, Any]]] = {}  # db path -> {data_version: rows}
_HISTORY_KEEP = 8


def _key() -> str:
    return str(db.db_path())


def _read_rows(conn) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in conn.execute("SELECT key, value FROM settings").fetchall():
        try:
            out[str(key)] = json.loads(value)
        except ValueError:
            continue
    return out


def _remember(key: str, version: int, rows: dict[str, Any]) -> None:
    _snapshot[key] = rows
    _snapshot_version[key] = version
    hist = _history.setdefault(key, {})
    hist[version] = rows
    for old in sorted(hist)[:-_HISTORY_KEEP]:
        hist.pop(old, None)


def load_fields_versioned() -> tuple[dict[str, Any], int]:
    """All persisted fields plus the data_version they were read at.

    A caller that keeps the version can later save against exactly the rows it
    saw, even if another thread refreshed the process snapshot in between.
    """
    conn = db.connect()
    key = _key()
    version = db.data_version(conn)
    with _lock:
        if _snapshot_version.get(key) == version and key in _snapshot:
            return dict(_snapshot[key]), version
    rows = _read_rows(conn)
    with _lock:
        _remember(key, version, rows)
    return dict(rows), version


def load_fields() -> dict[str, Any]:
    """All persisted fields as a fresh dict (never the cached object)."""
    return load_fields_versioned()[0]


def save_fields(
    new_values: dict[str, Any], defaults: dict[str, Any], *, origin_version: int | None = None
) -> list[str]:
    """Write only the keys whose value differs from the rows the caller loaded.

    *origin_version* is the data_version returned by :func:`load_fields_versioned`
    for the object being saved; without it the process's latest snapshot is used.
    A value equal to its default deletes the row. Returns the keys written.
    """
    conn = db.connect()
    key = _key()
    with _lock:
        seen: dict[str, Any] | None = None
        if origin_version is not None:
            seen = _history.get(key, {}).get(origin_version)
        if seen is None:
            seen = _snapshot.get(key)
        seen = dict(seen or {})
    changed: list[str] = []
    for name, value in new_values.items():
        before = seen.get(name, defaults.get(name))
        if value != before:
            changed.append(name)
    if not changed:
        return []
    now = time.time()
    with db.write_txn(conn):
        for name in changed:
            value = new_values[name]
            if name in defaults and value == defaults[name]:
                conn.execute("DELETE FROM settings WHERE key=?", (name,))
            else:
                conn.execute(
                    "INSERT INTO settings(key, value, updated) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                    (name, json.dumps(value, ensure_ascii=False), now),
                )
    rows = _read_rows(conn)
    with _lock:
        _remember(key, db.data_version(conn), rows)
    return changed


def replace_all(values: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Importer entry: set the table to exactly *values* (non-defaults only)."""
    conn = db.connect()
    now = time.time()
    with db.write_txn(conn):
        conn.execute("DELETE FROM settings")
        for name, value in values.items():
            if name in defaults and value == defaults[name]:
                continue
            conn.execute(
                "INSERT INTO settings(key, value, updated) VALUES (?, ?, ?)",
                (name, json.dumps(value, ensure_ascii=False), now),
            )
    with _lock:
        _snapshot.pop(_key(), None)
        _snapshot_version.pop(_key(), None)
        _history.pop(_key(), None)


def has_rows() -> bool:
    return db.connect().execute("SELECT 1 FROM settings LIMIT 1").fetchone() is not None


def reset_for_tests() -> None:
    with _lock:
        _snapshot.clear()
        _snapshot_version.clear()
        _history.clear()
