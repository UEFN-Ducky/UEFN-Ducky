"""``projects`` table: one row per UEFN project Ducky has opened.

Replaces ``recent_projects.json``. ``id`` is the project slug every
project-scoped store already uses (``project_chats.project_slug``).
"""

from __future__ import annotations

import time

from backend.store import db

MAX_RECENT = 20


def slug_for(path: str) -> str:
    from frontend.ui_web.project_chats import project_slug

    return project_slug(path)


def recent_paths(limit: int = MAX_RECENT) -> list[str]:
    rows = db.connect().execute(
        "SELECT path FROM projects WHERE deleted=0 ORDER BY last_opened DESC, path LIMIT ?",
        (limit,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def touch(path: str, *, name: str = "", when: float | None = None) -> str:
    """Record an open; returns the slug."""
    slug = slug_for(path)
    now = when if when is not None else time.time()
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO projects(id, path, name, last_opened, created, deleted) VALUES (?, ?, ?, ?, ?, 0) "
            "ON CONFLICT(id) DO UPDATE SET path=excluded.path, last_opened=excluded.last_opened, "
            "deleted=0, name=CASE WHEN excluded.name='' THEN projects.name ELSE excluded.name END",
            (slug, path, name, now, now),
        )
    return slug


def forget(path: str) -> None:
    """Drop from the recent list (the row stays, marked deleted, so history keeps its FK)."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("UPDATE projects SET deleted=1 WHERE id=?", (slug_for(path),))


def replace_recent(paths: list[str]) -> None:
    """Importer entry: the ordered legacy list becomes descending last_opened stamps."""
    conn = db.connect()
    base = time.time()
    with db.write_txn(conn):
        for index, path in enumerate(paths):
            stamp = base - index
            conn.execute(
                "INSERT INTO projects(id, path, name, last_opened, created, deleted) VALUES (?, ?, '', ?, ?, 0) "
                "ON CONFLICT(id) DO UPDATE SET path=excluded.path, last_opened=excluded.last_opened, deleted=0",
                (slug_for(path), path, stamp, stamp),
            )


def has_rows() -> bool:
    return db.connect().execute("SELECT 1 FROM projects LIMIT 1").fetchone() is not None
