"""``secrets`` table: one DPAPI blob per name. Plaintext never touches the file.

The caller (``backend/agent/secrets.py``) owns encryption; this module only
stores and returns the ciphertext bytes it is handed.
"""

from __future__ import annotations

import time

from backend.store import db


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


def delete(name: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM secrets WHERE name=?", (name,))


def replace_all(blobs: dict[str, bytes]) -> None:
    conn = db.connect()
    now = time.time()
    with db.write_txn(conn):
        conn.execute("DELETE FROM secrets")
        for name, blob in blobs.items():
            conn.execute("INSERT INTO secrets(name, blob, updated) VALUES (?, ?, ?)", (name, blob, now))


def has_rows() -> bool:
    return db.connect().execute("SELECT 1 FROM secrets LIMIT 1").fetchone() is not None
