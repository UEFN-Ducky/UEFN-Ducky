"""Phase 5/6 repos: MCP servers, capture index, Verse diagnostics cache, Verse digest index."""

from __future__ import annotations

import json
import time
from typing import Any

from backend.store import db

# --------------------------------------------------------------------------- mcp servers


def mcp_servers_get() -> dict[str, dict[str, Any]]:
    rows = db.connect().execute("SELECT id, block FROM mcp_servers ORDER BY id").fetchall()
    out: dict[str, dict[str, Any]] = {}
    for sid, block in rows:
        try:
            out[str(sid)] = json.loads(block)
        except ValueError:
            continue
    return out


def mcp_servers_replace(servers: dict[str, dict[str, Any]]) -> None:
    conn = db.connect()
    current = mcp_servers_get()
    now = time.time()
    with db.write_txn(conn):
        # "A config exists" even when it holds zero servers — the old file could be
        # ``{"mcpServers": {}}``; without this every boot would re-seed the catalog.
        conn.execute(
            "INSERT INTO meta(key, value, updated) VALUES ('mcp_servers_seeded', '1', ?) ON CONFLICT(key) DO NOTHING",
            (now,),
        )
        for sid in set(current) - set(servers):
            conn.execute("DELETE FROM mcp_servers WHERE id=?", (sid,))
        for sid, block in servers.items():
            if current.get(sid) == block:
                continue
            conn.execute(
                "INSERT INTO mcp_servers(id, block, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET block=excluded.block, updated=excluded.updated",
                (sid, json.dumps(block, ensure_ascii=False), now),
            )


def mcp_servers_present() -> bool:
    conn = db.connect()
    if conn.execute("SELECT 1 FROM mcp_servers LIMIT 1").fetchone() is not None:
        return True
    return conn.execute("SELECT 1 FROM meta WHERE key='mcp_servers_seeded'").fetchone() is not None


def mcp_servers_reset_for_tests() -> None:
    """Drop the server rows and the seeded marker (tests only).

    ``mcp_servers_present()`` is deliberately sticky: the marker survives an
    empty server map so boot does not re-seed the catalog every time. One pytest
    session shares one ducky.db, so the first test that writes servers flips that
    marker for every test after it — and a test that sets up only an ``mcp.json``
    then silently reads its predecessors' rows instead of its own file.
    """
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM mcp_servers")
        conn.execute("DELETE FROM meta WHERE key='mcp_servers_seeded'")


# --------------------------------------------------------------------------- captures


def capture_add(filename: str, *, prefix: str, nbytes: int) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO captures(filename, prefix, bytes, created) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(filename) DO UPDATE SET prefix=excluded.prefix, bytes=excluded.bytes, created=excluded.created",
            (filename, prefix, int(nbytes), time.time()),
        )


def capture_prune(keep: int) -> list[str]:
    """Drop rows beyond the newest *keep*; returns the filenames dropped."""
    conn = db.connect()
    stale = [
        str(r[0])
        for r in conn.execute(
            "SELECT filename FROM captures WHERE filename NOT IN (SELECT filename FROM captures ORDER BY created DESC LIMIT ?)",
            (int(keep),),
        ).fetchall()
    ]
    if stale:
        with db.write_txn(conn):
            conn.executemany("DELETE FROM captures WHERE filename=?", [(f,) for f in stale])
    return stale


def capture_names() -> set[str]:
    return {str(r[0]) for r in db.connect().execute("SELECT filename FROM captures").fetchall()}


# --------------------------------------------------------------------------- verse diagnostics


def diag_get(project_id: str) -> dict[str, dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT path, mtime_ns, size, errors, warnings, items FROM verse_diagnostics WHERE project_id=?", (project_id,)
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            items = json.loads(r["items"])
        except ValueError:
            items = []
        out[str(r["path"])] = {
            "mtime_ns": int(r["mtime_ns"]),
            "size": int(r["size"]),
            "errors": int(r["errors"]),
            "warnings": int(r["warnings"]),
            "items": items,
        }
    return out


def diag_replace(project_id: str, files: dict[str, dict[str, Any]]) -> None:
    conn = db.connect()
    current = diag_get(project_id)
    now = time.time()
    with db.write_txn(conn):
        for path in set(current) - set(files):
            conn.execute("DELETE FROM verse_diagnostics WHERE project_id=? AND path=?", (project_id, path))
        for path, entry in files.items():
            if not isinstance(entry, dict):
                continue
            slim = {
                "mtime_ns": int(entry.get("mtime_ns") or 0),
                "size": int(entry.get("size") or 0),
                "errors": int(entry.get("errors") or 0),
                "warnings": int(entry.get("warnings") or 0),
                "items": entry.get("items") or [],
            }
            if current.get(path) == slim:
                continue
            conn.execute(
                "INSERT INTO verse_diagnostics(project_id, path, mtime_ns, size, errors, warnings, items, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, path) DO UPDATE SET mtime_ns=excluded.mtime_ns, "
                "size=excluded.size, errors=excluded.errors, warnings=excluded.warnings, items=excluded.items, "
                "updated=excluded.updated",
                (project_id, path, slim["mtime_ns"], slim["size"], slim["errors"], slim["warnings"],
                 json.dumps(slim["items"], ensure_ascii=False), now),
            )


def diag_stamp(project_id: str) -> tuple[int, int] | None:
    """Changes whenever any process rewrote this project's rows (replaces the file mtime/size check)."""
    row = db.connect().execute(
        "SELECT count(*), coalesce(max(updated), 0) FROM verse_diagnostics WHERE project_id=?", (project_id,)
    ).fetchone()
    if not row or int(row[0]) == 0:
        return None
    return int(float(row[1]) * 1_000_000), int(row[0])


def diag_clear(project_id: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM verse_diagnostics WHERE project_id=?", (project_id,))


# --------------------------------------------------------------------------- verse digests


def digest_indexed_mtime(path: str) -> float | None:
    row = db.connect().execute("SELECT mtime FROM digest_files WHERE path=?", (path,)).fetchone()
    return None if row is None else float(row[0])


def digest_index(path: str, mtime: float, lines: list[str]) -> None:
    """(Re)index one digest: replace its rows when the file changed."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM digest_lines WHERE path=?", (path,))
        conn.execute(
            "INSERT INTO digest_files(path, mtime, lines, indexed) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, lines=excluded.lines, indexed=excluded.indexed",
            (path, float(mtime), len(lines), time.time()),
        )
        conn.executemany(
            "INSERT INTO digest_lines(path, line, text) VALUES (?, ?, ?)",
            [(path, i + 1, line.rstrip("\r\n")) for i, line in enumerate(lines) if line.strip()],
        )


def digest_search(query: str, paths: list[str], *, limit: int = 2000) -> list[tuple[str, int, str]]:
    """Substring (trigram) search over indexed digest lines restricted to *paths*."""
    q = query.strip()
    if len(q) < 3 or not paths:
        return []
    marks = ",".join("?" for _ in paths)
    escaped = q.replace('"', '""')
    rows = db.connect().execute(
        f"SELECT l.path, l.line, l.text FROM digest_fts f JOIN digest_lines l ON l.id = f.rowid "
        f"WHERE digest_fts MATCH ? AND l.path IN ({marks}) LIMIT ?",
        (f'"{escaped}"', *paths, limit),
    ).fetchall()
    return [(str(r[0]), int(r[1]), str(r[2])) for r in rows]


def digest_drop_missing(existing_paths: set[str]) -> int:
    conn = db.connect()
    known = [str(r[0]) for r in conn.execute("SELECT path FROM digest_files").fetchall()]
    gone = [p for p in known if p not in existing_paths]
    if gone:
        with db.write_txn(conn):
            for p in gone:
                conn.execute("DELETE FROM digest_lines WHERE path=?", (p,))
                conn.execute("DELETE FROM digest_files WHERE path=?", (p,))
    return len(gone)
