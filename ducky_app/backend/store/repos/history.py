"""``file_versions`` + ``blobs``: per-file version history (phase 3).

One row per version; the text lives once in ``blobs`` keyed by the same
sha256[:16] the change ledger uses, so an agent edit recorded by both stores
costs one copy.
"""

from __future__ import annotations

import time
from typing import Any

from backend.store import db
from backend.store.repos import ledger

ATTRIBUTION = ("source", "run_id", "conv_id", "profile_id", "ducky_name", "model", "tool", "group_id", "coding_agent")
MAX_PER_FILE = 100


def newest(project_id: str, path: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM file_versions WHERE project_id=? AND path=? ORDER BY entry_id DESC LIMIT 1", (project_id, path)
    ).fetchone()
    return None if row is None else dict(row)


def exists(project_id: str, path: str, entry_id: str) -> bool:
    return (
        db.connect()
        .execute("SELECT 1 FROM file_versions WHERE project_id=? AND path=? AND entry_id=?", (project_id, path, entry_id))
        .fetchone()
        is not None
    )


def put(project_id: str, path: str, entry_id: str, content: str, *, preview: str, saved_at: int,
        attribution: dict[str, Any], schema_version: int = 2, content_hash: str | None = None) -> str:
    digest = ledger.blob_put(content, digest=content_hash)
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO file_versions(project_id, path, entry_id, saved_at, bytes, preview, content_hash, "
            "schema_version, source, run_id, conv_id, profile_id, ducky_name, model, tool, group_id, coding_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, path, entry_id) DO UPDATE SET saved_at=excluded.saved_at, bytes=excluded.bytes, "
            "preview=excluded.preview, content_hash=excluded.content_hash, schema_version=excluded.schema_version, "
            "source=excluded.source, run_id=excluded.run_id, conv_id=excluded.conv_id, profile_id=excluded.profile_id, "
            "ducky_name=excluded.ducky_name, model=excluded.model, tool=excluded.tool, group_id=excluded.group_id, "
            "coding_agent=excluded.coding_agent",
            (
                project_id,
                path,
                entry_id,
                int(saved_at),
                len(content.encode("utf-8")),
                preview,
                digest,
                int(schema_version),
                *[str(attribution.get(k) or "") for k in ATTRIBUTION],
            ),
        )
        # Keep the newest MAX_PER_FILE (entry ids are ms timestamps).
        conn.execute(
            "DELETE FROM file_versions WHERE project_id=? AND path=? AND entry_id NOT IN "
            "(SELECT entry_id FROM file_versions WHERE project_id=? AND path=? ORDER BY entry_id DESC LIMIT ?)",
            (project_id, path, project_id, path, MAX_PER_FILE),
        )
    return digest


def list_rows(project_id: str, path: str) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT * FROM file_versions WHERE project_id=? AND path=? ORDER BY entry_id DESC", (project_id, path)
    ).fetchall()
    return [dict(r) for r in rows]


def content(project_id: str, path: str, entry_id: str) -> str | None:
    row = db.connect().execute(
        "SELECT b.text FROM file_versions v JOIN blobs b ON b.hash = v.content_hash "
        "WHERE v.project_id=? AND v.path=? AND v.entry_id=?",
        (project_id, path, entry_id),
    ).fetchone()
    return None if row is None else str(row[0])


def rename_path(project_id: str, old: str, new: str) -> int:
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute("UPDATE file_versions SET path=? WHERE project_id=? AND path=?", (new, project_id, old))
    return cur.rowcount


def now_ms_id() -> str:
    return f"{int(time.time() * 1000)}"
