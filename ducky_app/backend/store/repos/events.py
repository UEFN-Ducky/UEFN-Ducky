"""``events``: the rolling logs (errors, activity, agent crashes, verse stats)."""

from __future__ import annotations

import json
from typing import Any

from backend.store import db


def insert(kind: str, *, ts: float, source: str = "", message: str = "", payload: dict[str, Any] | None = None) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO events(kind, ts, source, message, payload) VALUES (?, ?, ?, ?, ?)",
            (kind, float(ts), source, message, None if payload is None else json.dumps(payload, ensure_ascii=False)),
        )


def insert_many(kind: str, rows: list[dict[str, Any]]) -> int:
    conn = db.connect()
    with db.write_txn(conn):
        conn.executemany(
            "INSERT INTO events(kind, ts, source, message, payload) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    kind,
                    float(r.get("ts") or 0.0),
                    str(r.get("source") or ""),
                    str(r.get("message") or ""),
                    None if r.get("payload") is None else json.dumps(r["payload"], ensure_ascii=False),
                )
                for r in rows
            ],
        )
    return len(rows)


def newest(kind: str, *, limit: int, since: float | None = None) -> list[dict[str, Any]]:
    conn = db.connect()
    if since is None:
        rows = conn.execute(
            "SELECT ts, source, message, payload FROM events WHERE kind=? ORDER BY ts DESC, id DESC LIMIT ?", (kind, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ts, source, message, payload FROM events WHERE kind=? AND ts>=? ORDER BY ts DESC, id DESC LIMIT ?",
            (kind, since, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row: dict[str, Any] = {"ts": float(r["ts"]), "source": str(r["source"]), "message": str(r["message"])}
        if r["payload"]:
            try:
                row["payload"] = json.loads(r["payload"])
            except ValueError:
                pass
        out.append(row)
    return out


def last_message(kind: str) -> str | None:
    row = db.connect().execute("SELECT message FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    return None if row is None else str(row[0])


def clear(kind: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM events WHERE kind=?", (kind,))


def trim(kind: str, *, older_than: float, keep: int) -> int:
    conn = db.connect()
    with db.write_txn(conn):
        cur = conn.execute("DELETE FROM events WHERE kind=? AND ts < ?", (kind, older_than))
        n = cur.rowcount
        cur = conn.execute(
            "DELETE FROM events WHERE kind=? AND id NOT IN (SELECT id FROM events WHERE kind=? ORDER BY id DESC LIMIT ?)",
            (kind, kind, keep),
        )
        n += cur.rowcount
    return n


def count(kind: str) -> int:
    return int(db.connect().execute("SELECT count(*) FROM events WHERE kind=?", (kind,)).fetchone()[0])
