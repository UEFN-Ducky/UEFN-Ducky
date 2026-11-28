"""``settings`` table: one row per :class:`PanelSettings` field, absence = default.

Reads are served from a per-process snapshot. ``PRAGMA data_version`` is defined
per connection, so each connection remembers the value it last saw the snapshot
valid at; when it moved (any commit by another connection, to any table) a cheap
signature of the table (row count, newest ``updated``, total value length) decides
whether the rows are read again. Writes are per-key: :func:`save_fields` compares the new
values with the snapshot this process last saw and touches only the keys the
caller actually changed, so two processes editing different fields never
overwrite each other (the lost update ``PanelSettings.load()`` used to cause).
"""

from __future__ import annotations

import copy
import itertools
import json
import threading
import time
from typing import Any

from backend.store import db

_lock = threading.Lock()
_snapshot: dict[str, dict[str, Any]] = {}  # db path -> {field: value}
_snapshot_version: dict[str, int] = {}  # db path -> id of the snapshot (bumped on every read or save)
_snapshot_sig: dict[str, tuple] = {}  # db path -> table signature the snapshot was read at
_history: dict[str, dict[int, dict[str, Any]]] = {}  # db path -> {snapshot id: rows}
_HISTORY_KEEP = 8
_ids = itertools.count(1)
# Per thread: db path -> (connection, snapshot id, data_version it last saw that snapshot
# valid at). Thread-local, so it holds the connection (no id() reuse) and goes with the
# thread; snapshot ids never repeat, so an entry left from before a reset never matches.
_seen = threading.local()


def _seen_map() -> dict[str, tuple[Any, int, int]]:
    m = getattr(_seen, "m", None)
    if m is None:
        m = _seen.m = {}
    return m

# Duckies lived in these fields before they got their own table (0014). They are no
# longer saved: the first account's seal pass moves them, sealed, into ``meta``
# (``account_rows``), and a stale in-memory copy must never write them back.
LEGACY_DUCKY_KEYS = (
    "agent_profiles",
    "agent_profile_overrides",
    "archived_agent_profile_ids",
    "hidden_bundled_agent_profile_ids",
    "agent_profile_visibility_explicit",
)


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


def _signature(conn) -> tuple:
    return tuple(conn.execute("SELECT count(*), max(updated), total(length(value)) FROM settings").fetchone())


def _remember(key: str, conn, dv: int, rows: dict[str, Any], sig: tuple) -> int:
    """*dv* and *sig* were taken before *rows*: a commit in between only makes the next
    load read again, never trust older rows."""
    version = next(_ids)
    _snapshot[key] = rows
    _snapshot_version[key] = version
    _snapshot_sig[key] = sig
    _seen_map()[key] = (conn, version, dv)
    hist = _history.setdefault(key, {})
    hist[version] = rows
    for old in sorted(hist)[:-_HISTORY_KEEP]:
        hist.pop(old, None)
    return version


def load_fields_versioned() -> tuple[dict[str, Any], int]:
    """All persisted fields plus the id of the snapshot they came from.

    A caller that keeps the id can later save against exactly the rows it
    saw, even if another thread refreshed the process snapshot in between.
    """
    conn = db.connect()
    key = _key()
    dv = db.data_version(conn)
    with _lock:
        version = _snapshot_version.get(key)
        seen = _seen_map().get(key)
        if version is not None and key in _snapshot and seen and seen[0] is conn and seen[1:] == (version, dv):
            return copy.deepcopy(_snapshot[key]), version
    sig = _signature(conn)
    with _lock:
        version = _snapshot_version.get(key)
        if version is not None and key in _snapshot and _snapshot_sig.get(key) == sig:
            _seen_map()[key] = (conn, version, dv)
            return copy.deepcopy(_snapshot[key]), version
    rows = _read_rows(conn)
    with _lock:
        version = _remember(key, conn, dv, rows, sig)
    # A deep copy: a caller that edits a list or dict in place (renaming an appearance
    # profile) must not edit the snapshot its save is compared with, or nothing is saved.
    return copy.deepcopy(rows), version


def load_fields() -> dict[str, Any]:
    """All persisted fields as a fresh dict (never the cached object)."""
    return load_fields_versioned()[0]


def save_fields(
    new_values: dict[str, Any], defaults: dict[str, Any], *, origin_version: int | None = None
) -> list[str]:
    """Write only the keys whose value differs from the rows the caller loaded.

    *origin_version* is the snapshot id returned by :func:`load_fields_versioned`
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
        if name in LEGACY_DUCKY_KEYS:
            continue
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
    dv = db.data_version(conn)
    sig = _signature(conn)
    rows = _read_rows(conn)
    with _lock:
        _remember(key, conn, dv, rows, sig)
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
    forget_snapshot()


def forget_snapshot() -> None:
    """Rows changed outside :func:`save_fields` (the legacy ducky fields moved away)."""
    with _lock:
        _snapshot.pop(_key(), None)
        _snapshot_version.pop(_key(), None)
        _snapshot_sig.pop(_key(), None)
        _history.pop(_key(), None)


def has_rows() -> bool:
    return db.connect().execute("SELECT 1 FROM settings LIMIT 1").fetchone() is not None


def reset_for_tests() -> None:
    with _lock:
        _snapshot.clear()
        _snapshot_version.clear()
        _snapshot_sig.clear()
        _history.clear()
        _seen_map().clear()
