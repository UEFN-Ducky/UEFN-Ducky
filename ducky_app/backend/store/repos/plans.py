"""``plans`` (project plans keyed by chat id; templates keyed by template id) and ``tasks``."""

from __future__ import annotations

import json
from typing import Any

from backend.store import db

TEMPLATE_PROJECT = ""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def plan_get(project_id: str, key: str) -> dict[str, Any] | None:
    row = db.connect().execute("SELECT doc FROM plans WHERE project_id=? AND key=?", (project_id, key)).fetchone()
    return None if row is None else json.loads(row[0])


def plan_put(project_id: str, key: str, kind: str, doc: dict[str, Any]) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO plans(project_id, key, kind, plan_id, title, status, updated_at, doc) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, key) DO UPDATE SET kind=excluded.kind, plan_id=excluded.plan_id, title=excluded.title, "
            "status=excluded.status, updated_at=excluded.updated_at, doc=excluded.doc",
            (
                project_id,
                key,
                kind,
                str(doc.get("id") or ""),
                str(doc.get("title") or ""),
                str(doc.get("status") or ""),
                float(doc.get("updated_at") or doc.get("created_at") or 0.0),
                _dumps(doc),
            ),
        )


def plan_delete(project_id: str, key: str) -> bool:
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute("DELETE FROM plans WHERE project_id=? AND key=?", (project_id, key))
    return cur.rowcount > 0


def plan_docs(project_id: str, kind: str) -> list[tuple[str, dict[str, Any]]]:
    rows = db.connect().execute(
        "SELECT key, doc FROM plans WHERE project_id=? AND kind=? ORDER BY key", (project_id, kind)
    ).fetchall()
    return [(str(k), json.loads(d)) for k, d in rows]


def plan_head(project_id: str, key: str) -> dict[str, Any] | None:
    """The mutator gate's two columns without deserialising the tree."""
    row = db.connect().execute(
        "SELECT plan_id, status, updated_at FROM plans WHERE project_id=? AND key=?", (project_id, key)
    ).fetchone()
    return None if row is None else dict(row)


# --------------------------------------------------------------------------- tasks


def task_get(project_id: str, task_id: str) -> dict[str, Any] | None:
    row = db.connect().execute("SELECT doc FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)).fetchone()
    return None if row is None else json.loads(row[0])


def task_put(project_id: str, doc: dict[str, Any]) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO tasks(project_id, id, updated, doc) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_id, id) DO UPDATE SET updated=excluded.updated, doc=excluded.doc",
            (project_id, str(doc["id"]), float(doc.get("updated") or 0.0), _dumps(doc)),
        )


def task_docs(project_id: str) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT doc FROM tasks WHERE project_id=? ORDER BY updated DESC, id", (project_id,)
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def task_delete(project_id: str, task_id: str) -> bool:
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute("DELETE FROM tasks WHERE project_id=? AND id=?", (project_id, task_id))
    return cur.rowcount > 0
