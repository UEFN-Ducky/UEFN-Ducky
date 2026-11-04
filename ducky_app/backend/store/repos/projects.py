"""``projects`` table: one row per project (UEFN island or plain folder) Ducky has opened.

Replaces ``recent_projects.json``. ``id`` is the project slug every
project-scoped store already uses (``project_chats.project_slug``).
"""

from __future__ import annotations

import time

from backend.store import db

MAX_RECENT = 20


def slug_for(path: str) -> str:
    from frontend.ui_web.chats.store import project_slug

    return project_slug(path)


def recent_paths(limit: int = MAX_RECENT) -> list[str]:
    rows = db.connect().execute(
        "SELECT path FROM projects WHERE deleted=0 ORDER BY last_opened DESC, path LIMIT ?",
        (limit,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def opened_rows() -> list[tuple[str, float]]:
    """Every saved project (no recent cap), newest first: ``(path, last_opened)``."""
    rows = db.connect().execute(
        "SELECT path, last_opened FROM projects WHERE deleted=0 ORDER BY last_opened DESC, path"
    ).fetchall()
    return [(str(r[0]), float(r[1] or 0)) for r in rows]


def removed_paths() -> list[str]:
    """Projects the person removed: discovery keeps them out of the project list."""
    rows = db.connect().execute("SELECT path FROM projects WHERE deleted=1").fetchall()
    return [str(r[0]) for r in rows]


def touch(path: str, *, name: str = "", when: float | None = None, kind: str = "") -> str:
    """Record an open; returns the slug.

    ``kind`` ('uefn' / 'folder') is stored on first open and may upgrade a folder to an
    island later. An island never becomes a folder here; an empty kind keeps the row's.
    """
    slug = slug_for(path)
    now = when if when is not None else time.time()
    kind = kind if kind in ("uefn", "folder") else ""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO projects(id, path, name, last_opened, created, deleted, kind) "
            "VALUES (?, ?, ?, ?, ?, 0, COALESCE(NULLIF(?, ''), 'uefn')) "
            "ON CONFLICT(id) DO UPDATE SET path=excluded.path, last_opened=excluded.last_opened, "
            "deleted=0, name=CASE WHEN excluded.name='' THEN projects.name ELSE excluded.name END, "
            "kind=CASE WHEN projects.kind='uefn' OR ?='' THEN projects.kind ELSE excluded.kind END",
            (slug, path, name, now, now, kind, kind),
        )
    return slug


def kind_for(path: str) -> str:
    """Stored kind for *path* ('' when Ducky never recorded it). Removed rows still answer."""
    row = db.connect().execute("SELECT kind FROM projects WHERE id=?", (slug_for(path),)).fetchone()
    return str(row[0]) if row and row[0] else ""


def forget(path: str) -> None:
    """Drop from the recent list (the row stays, marked deleted, so history keeps its FK).
    A discovered island never opened gets a deleted row so discovery leaves it out."""
    conn = db.connect()
    now = time.time()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO projects(id, path, name, last_opened, created, deleted) VALUES (?, ?, '', 0, ?, 1) "
            "ON CONFLICT(id) DO UPDATE SET deleted=1",
            (slug_for(path), path, now),
        )


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
