"""``secrets`` table: one DPAPI blob per name. Plaintext never touches the file.

The caller (``backend/agent/secrets.py``) owns encryption; this module only
stores and returns the ciphertext bytes it is handed.

A write or delete of the DuckyOS login row bumps ``meta.login_version`` in the same
transaction, so every process can tell with one tiny read that the login changed.
"""

from __future__ import annotations

import time

from backend.store import db

LOGIN_NAME = "duckyos_account"  # frontend.duckyos_account._CREDENTIALS_KEY
LOGIN_VERSION_KEY = "login_version"
_login_writes = 0  # login row writes committed by this process: memos of the login key on it


def login_writes() -> int:
    return _login_writes


def _wrote_login() -> None:
    global _login_writes
    _login_writes += 1


def _bump_login_version(conn) -> None:
    conn.execute(
        "INSERT INTO meta(key, value, updated) VALUES (?, '1', ?) ON CONFLICT(key) DO UPDATE SET "
        "value=CAST(CAST(value AS INTEGER) + 1 AS TEXT), updated=excluded.updated",
        (LOGIN_VERSION_KEY, time.time()),
    )


def login_version() -> str:
    """Changes whenever the login row is written or deleted; ``"0"`` before the first write."""
    row = db.connect().execute("SELECT value FROM meta WHERE key=?", (LOGIN_VERSION_KEY,)).fetchone()
    return "0" if row is None else str(row[0])


def get_blob(name: str) -> bytes | None:
    row = db.connect().execute("SELECT blob FROM secrets WHERE name=?", (name,)).fetchone()
    return None if row is None else bytes(row[0])


def all_blobs() -> dict[str, bytes]:
    rows = db.connect().execute("SELECT name, blob FROM secrets ORDER BY name").fetchall()
    return {str(name): bytes(blob) for name, blob in rows}


def set_blob(name: str, blob: bytes) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO secrets(name, blob, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET blob=excluded.blob, updated=excluded.updated",
            (name, blob, time.time()),
        )
        if name == LOGIN_NAME:
            _bump_login_version(conn)
    if name == LOGIN_NAME:
        _wrote_login()


def delete(name: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM secrets WHERE name=?", (name,))
        if name == LOGIN_NAME:
            _bump_login_version(conn)
    if name == LOGIN_NAME:
        _wrote_login()


def replace_all(blobs: dict[str, bytes]) -> None:
    conn = db.connect()
    now = time.time()
    with db.write_txn(conn):
        conn.execute("DELETE FROM secrets")
        for name, blob in blobs.items():
            conn.execute("INSERT INTO secrets(name, blob, updated) VALUES (?, ?, ?)", (name, blob, now))
        _bump_login_version(conn)  # an import or restore may change the login
    _wrote_login()


def has_rows() -> bool:
    return db.connect().execute("SELECT 1 FROM secrets LIMIT 1").fetchone() is not None
