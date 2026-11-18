"""``plans`` (project plans keyed by chat id; templates keyed by template id) and ``tasks``."""

from __future__ import annotations

import json
from typing import Any

from backend.store import db

TEMPLATE_PROJECT = ""
# A chat's plan lives with its ducky, in the one list, not in a project (Oct 23 2026: duckies
# work on any project, so switching the selected project must not hide a ducky's plan).
ONE_LIST_PROJECT = "_no_project"
_merged: set[str] = set()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def merge_into_one_list(*, again: bool = False) -> int:
    """Move chat plans saved under a project into the one list: once per database, or
    ``again`` after a legacy import added some. A chat with plans in several places keeps
    the newest there; the others stay where they are. Nothing is deleted."""
    key = str(db.db_path())
    if key in _merged and not again:
        return 0
    conn = db.connect()
    rows = conn.execute(
        "SELECT project_id, key, updated_at FROM plans WHERE kind='project' AND project_id NOT IN (?, ?) "
        "ORDER BY updated_at DESC",
        (ONE_LIST_PROJECT, TEMPLATE_PROJECT),
    ).fetchall()
    moved = 0
    with db.write_txn(conn):
        for project_id, plan_key, updated in rows:
            have = conn.execute(
                "SELECT updated_at FROM plans WHERE project_id=? AND key=?", (ONE_LIST_PROJECT, plan_key)
            ).fetchone()
            if have is not None:
                if float(have[0] or 0) >= float(updated or 0):
                    continue
                # The project's plan is newer: the two swap places, both kept.
                conn.execute(
                    "UPDATE plans SET project_id=? WHERE project_id=? AND key=?",
                    ("\x00swap", ONE_LIST_PROJECT, plan_key),
                )
                conn.execute(
                    "UPDATE plans SET project_id=? WHERE project_id=? AND key=?",
                    (ONE_LIST_PROJECT, project_id, plan_key),
                )
                conn.execute(
                    "UPDATE plans SET project_id=? WHERE project_id=? AND key=?", (project_id, "\x00swap", plan_key)
                )
            else:
                conn.execute(
                    "UPDATE plans SET project_id=? WHERE project_id=? AND key=?",
                    (ONE_LIST_PROJECT, project_id, plan_key),
                )
            moved += 1
    _merged.add(key)
    return moved


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

