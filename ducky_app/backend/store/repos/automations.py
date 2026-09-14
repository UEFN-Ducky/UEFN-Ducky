"""``automations`` table: one row per workflow graph."""

from __future__ import annotations

import json
from typing import Any

from backend.store import db


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def get(workflow_id: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT id, name, enabled, graph, runs, updated, last_run FROM automations WHERE id=?",
        (workflow_id,),
    ).fetchone()
    return None if row is None else _row(row)


def put(doc: dict[str, Any]) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO automations(id, name, enabled, graph, runs, updated, last_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, enabled=excluded.enabled, "
            "graph=excluded.graph, runs=excluded.runs, updated=excluded.updated, last_run=excluded.last_run",
            (
                str(doc["id"]),
                str(doc.get("name") or ""),
                1 if doc.get("enabled", True) else 0,
                _dumps(doc.get("graph") or {"nodes": [], "edges": []}),
                _dumps(doc.get("runs") or []),
                float(doc.get("updated") or 0.0),
                float(doc.get("last_run") or 0.0),
            ),
        )


def delete(workflow_id: str) -> bool:
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute("DELETE FROM automations WHERE id=?", (workflow_id,))
    return cur.rowcount > 0


def list_all() -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT id, name, enabled, graph, runs, updated, last_run FROM automations ORDER BY updated DESC, name"
    ).fetchall()
    return [_row(r) for r in rows]


def _row(row: Any) -> dict[str, Any]:
    try:
        graph = json.loads(row[3])
    except ValueError:
        graph = {"nodes": [], "edges": []}
    if not isinstance(graph, dict):
        graph = {"nodes": [], "edges": []}
    try:
        runs = json.loads(row[4])
    except ValueError:
        runs = []
    if not isinstance(runs, list):
        runs = []
    return {
        "id": str(row[0]),
        "name": str(row[1] or ""),
        "enabled": bool(int(row[2] or 0)),
        "graph": graph,
        "runs": runs,
        "updated": float(row[5] or 0.0),
        "last_run": float(row[6] or 0.0),
    }
