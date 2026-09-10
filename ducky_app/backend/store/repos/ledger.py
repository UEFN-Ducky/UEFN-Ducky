"""``runs`` / ``run_entries`` / ``run_seen`` / ``path_index`` / ``blobs`` / ``watch_index``.

The journal (``backend/workspace/journal.py``) keeps its dict-shaped run
documents and all of its revert semantics; this module is the storage it used
to get from ``runs/<id>.json``, ``index.json``, ``catalog.json`` and
``blobs/<hash>.txt``. ``project_id`` is the ledger folder name the journal
already derives (the project slug).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from backend.store import db

_RUN_DOC_EXCLUDED = ("entries", "seen")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- runs


def _assemble(conn, row) -> dict[str, Any]:
    run = json.loads(row["doc"])
    run["run_id"] = row["run_id"]
    entries = conn.execute("SELECT doc FROM run_entries WHERE run_id=? ORDER BY seq", (row["run_id"],)).fetchall()
    run["entries"] = [json.loads(e[0]) for e in entries]
    seen = conn.execute("SELECT path, doc FROM run_seen WHERE run_id=?", (row["run_id"],)).fetchall()
    run["seen"] = {str(p): json.loads(d) for p, d in seen}
    return run


def run_get(project_id: str, run_id: str) -> dict[str, Any] | None:
    conn = db.connect()
    row = conn.execute("SELECT * FROM runs WHERE run_id=? AND project_id=?", (run_id, project_id)).fetchone()
    return None if row is None else _assemble(conn, row)


def runs_docs(project_id: str) -> list[dict[str, Any]]:
    """Every run document in a project (the old ``glob('runs/*.json')``)."""
    conn = db.connect()
    rows = conn.execute("SELECT * FROM runs WHERE project_id=? ORDER BY started DESC", (project_id,)).fetchall()
    return [_assemble(conn, r) for r in rows]


def run_put(project_id: str, run: dict[str, Any]) -> None:
    """Upsert the run row; sync entries by content hash; replace the seen map."""
    conn = db.connect()
    doc = {k: v for k, v in run.items() if k not in _RUN_DOC_EXCLUDED}
    run_id = str(run["run_id"])
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO runs(run_id, project_id, conv_id, group_id, started, ended, status, archived, doc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET project_id=excluded.project_id, "
            "conv_id=excluded.conv_id, group_id=excluded.group_id, started=excluded.started, ended=excluded.ended, "
            "status=excluded.status, archived=excluded.archived, doc=excluded.doc",
            (
                run_id,
                project_id,
                str(run.get("conv_id") or ""),
                str(run.get("group_id") or ""),
                float(run.get("started") or 0.0),
                run.get("ended"),
                str(run.get("status") or "running"),
                1 if run.get("archived") else 0,
                _dumps(doc),
            ),
        )
        existing = {int(r[0]): str(r[1]) for r in conn.execute("SELECT seq, hash FROM run_entries WHERE run_id=?", (run_id,))}
        wanted: set[int] = set()
        for entry in run.get("entries") or []:
            seq = int(entry.get("seq") or 0)
            wanted.add(seq)
            body = _dumps(entry)
            h = _hash(body)
            if existing.get(seq) == h:
                continue
            conn.execute(
                "INSERT INTO run_entries(run_id, seq, path, ts, outcome, before_blob, after_blob, hash, doc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, seq) DO UPDATE SET path=excluded.path, "
                "ts=excluded.ts, outcome=excluded.outcome, before_blob=excluded.before_blob, "
                "after_blob=excluded.after_blob, hash=excluded.hash, doc=excluded.doc",
                (
                    run_id,
                    seq,
                    str(entry.get("path") or ""),
                    float(entry.get("ts") or 0.0),
                    str(entry.get("outcome") or "ok"),
                    str(entry.get("before_blob") or ""),
                    str(entry.get("after_blob") or ""),
                    h,
                    body,
                ),
            )
        gone = [s for s in existing if s not in wanted]
        if gone:
            conn.executemany("DELETE FROM run_entries WHERE run_id=? AND seq=?", [(run_id, s) for s in gone])
        conn.execute("DELETE FROM run_seen WHERE run_id=?", (run_id,))
        seen = run.get("seen") or {}
        if isinstance(seen, dict) and seen:
            conn.executemany(
                "INSERT INTO run_seen(run_id, path, doc) VALUES (?, ?, ?)",
                [(run_id, str(p), _dumps(v)) for p, v in seen.items()],
            )


def run_delete(project_id: str, run_id: str) -> bool:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM run_entries WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_seen WHERE run_id=?", (run_id,))
        cur = conn.execute("DELETE FROM runs WHERE run_id=? AND project_id=?", (run_id, project_id))
    return cur.rowcount > 0


def catalog(project_id: str) -> dict[str, dict[str, Any]]:
    """The old ``catalog.json``, derived: run_id -> {conv_id, group_id, started, archived}."""
    rows = db.connect().execute(
        "SELECT run_id, conv_id, group_id, started, archived FROM runs WHERE project_id=?", (project_id,)
    ).fetchall()
    return {
        str(r["run_id"]): {
            "conv_id": str(r["conv_id"]),
            "group_id": str(r["group_id"]),
            "started": float(r["started"]),
            "archived": bool(r["archived"]),
        }
        for r in rows
    }


def has_runs(project_id: str) -> bool:
    return db.connect().execute("SELECT 1 FROM runs WHERE project_id=? LIMIT 1", (project_id,)).fetchone() is not None


def latest_entry_for_path(project_id: str, paths: list[str], *, since: float) -> dict[str, Any] | None:
    """Newest applied entry touching any of *paths* since *since* (the old parse-every-run)."""
    if not paths:
        return None
    marks = ",".join("?" for _ in paths)
    row = db.connect().execute(
        f"SELECT e.doc FROM run_entries e JOIN runs r ON r.run_id = e.run_id "
        f"WHERE r.project_id=? AND e.path IN ({marks}) AND e.ts>=? ORDER BY e.ts DESC LIMIT 1",
        (project_id, *paths, since),
    ).fetchone()
    return None if row is None else json.loads(row[0])


# --------------------------------------------------------------------------- path index


def index_get(project_id: str) -> dict[str, Any]:
    rows = db.connect().execute("SELECT path, doc FROM path_index WHERE project_id=?", (project_id,)).fetchall()
    return {str(p): json.loads(d) for p, d in rows}


def index_replace(project_id: str, index: dict[str, Any]) -> None:
    """Set the index to exactly *index*, touching only rows that changed."""
    conn = db.connect()
    current = index_get(project_id)
    with db.write_txn(conn):
        for path in set(current) - set(index):
            conn.execute("DELETE FROM path_index WHERE project_id=? AND path=?", (project_id, path))
        for path, stamp in index.items():
            if current.get(path) == stamp:
                continue
            conn.execute(
                "INSERT INTO path_index(project_id, path, doc) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, path) DO UPDATE SET doc=excluded.doc",
                (project_id, str(path), _dumps(stamp)),
            )


# --------------------------------------------------------------------------- blobs


def blob_put(text: str, *, digest: str | None = None) -> str:
    from backend.workspace.paths import content_hash

    h = digest or content_hash(text)
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO blobs(hash, text, size, created) VALUES (?, ?, ?, ?)",
            (h, text, len(text.encode("utf-8")), time.time()),
        )
    return h


def blob_get(digest: str) -> str | None:
    if not digest:
        return None
    row = db.connect().execute("SELECT text FROM blobs WHERE hash=?", (digest,)).fetchone()
    return None if row is None else str(row[0])


def blob_exists(digest: str) -> bool:
    return db.connect().execute("SELECT 1 FROM blobs WHERE hash=?", (digest,)).fetchone() is not None


def sweep_blobs() -> int:
    """Drop blobs no run entry, file version or message references (refcount query)."""
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute(
            "DELETE FROM blobs WHERE hash NOT IN (SELECT before_blob FROM run_entries WHERE before_blob<>'') "
            "AND hash NOT IN (SELECT after_blob FROM run_entries WHERE after_blob<>'') "
            "AND hash NOT IN (SELECT content_hash FROM file_versions)"
        )
    return cur.rowcount


# --------------------------------------------------------------------------- watch index


def watch_get(project_id: str) -> dict[str, str]:
    rows = db.connect().execute("SELECT path, hash FROM watch_index WHERE project_id=?", (project_id,)).fetchall()
    return {str(p): str(h) for p, h in rows}


def watch_replace(project_id: str, seen: dict[str, str]) -> None:
    conn = db.connect()
    current = watch_get(project_id)
    with db.write_txn(conn):
        for path in set(current) - set(seen):
            conn.execute("DELETE FROM watch_index WHERE project_id=? AND path=?", (project_id, path))
        for path, digest in seen.items():
            if current.get(path) == digest:
                continue
            conn.execute(
                "INSERT INTO watch_index(project_id, path, hash) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, path) DO UPDATE SET hash=excluded.hash",
                (project_id, path, digest),
            )
