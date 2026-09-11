"""``memory_entries`` + ``memory_fts``: project memory notes (phase 4).

``name`` is ``entry`` or ``entry/sub``; ``parent`` is ``''`` or ``entry``.
"""

from __future__ import annotations

from typing import Any

from backend.store import db


def get(project_id: str, name: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT name, parent, description, author, updated, body FROM memory_entries WHERE project_id=? AND name=?",
        (project_id, name),
    ).fetchone()
    return None if row is None else dict(row)


def put(project_id: str, name: str, *, description: str, author: str, updated: str, body: str) -> None:
    parent = name.split("/", 1)[0] if "/" in name else ""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO memory_entries(project_id, name, parent, description, author, updated, body) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, name) DO UPDATE SET description=excluded.description, "
            "author=excluded.author, updated=excluded.updated, body=excluded.body",
            (project_id, name, parent, description, author, updated, body),
        )


def delete(project_id: str, name: str, *, with_subs: bool) -> int:
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute("DELETE FROM memory_entries WHERE project_id=? AND name=?", (project_id, name))
        n = cur.rowcount
        if with_subs:
            cur = conn.execute("DELETE FROM memory_entries WHERE project_id=? AND parent=?", (project_id, name))
            n += cur.rowcount
    return n


def index(project_id: str) -> list[dict[str, Any]]:
    """Top-level entries with their sub index, newest-updated first; no bodies."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT name, parent, description, author, updated, length(body) AS chars FROM memory_entries "
        "WHERE project_id=? ORDER BY updated DESC, name",
        (project_id,),
    ).fetchall()
    tops: dict[str, dict[str, Any]] = {}
    subs: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        if r["parent"]:
            subs.setdefault(str(r["parent"]), []).append({"name": str(r["name"]), "description": str(r["description"])})
        else:
            tops[str(r["name"])] = {
                "name": str(r["name"]),
                "description": str(r["description"]),
                "author": str(r["author"]),
                "updated": str(r["updated"]),
                "chars": int(r["chars"] or 0),
                "subs": [],
            }
    for parent, items in subs.items():
        if parent in tops:
            tops[parent]["subs"] = sorted(items, key=lambda s: s["name"])
    return list(tops.values())


def subs_of(project_id: str, entry: str) -> list[dict[str, str]]:
    rows = db.connect().execute(
        "SELECT name, description FROM memory_entries WHERE project_id=? AND parent=? ORDER BY name", (project_id, entry)
    ).fetchall()
    return [{"name": str(r[0]), "description": str(r[1])} for r in rows]


def search(project_id: str, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    terms = [t.replace('"', '""') for t in query.split() if t.strip()]
    if not terms:
        return []
    q = " ".join(f'"{t}"' for t in terms)
    rows = db.connect().execute(
        "SELECT m.name, m.description, snippet(memory_fts, 2, '', '', '…', 12) AS preview "
        "FROM memory_fts JOIN memory_entries m ON m.rowid = memory_fts.rowid "
        "WHERE memory_fts MATCH ? AND m.project_id=? ORDER BY bm25(memory_fts) LIMIT ?",
        (q, project_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def has_rows(project_id: str) -> bool:
    return db.connect().execute("SELECT 1 FROM memory_entries WHERE project_id=? LIMIT 1", (project_id,)).fetchone() is not None
