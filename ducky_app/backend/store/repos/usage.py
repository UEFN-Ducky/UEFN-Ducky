"""``usage_calls``: one row per LLM / coding-agent call (phase 4)."""

from __future__ import annotations

from typing import Any

from backend.store import db

COLUMNS = (
    "ts", "provider", "model", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "conv_id", "agent", "ducky_label", "cost_usd",
)


def insert(entry: dict[str, Any], *, retention_s: float | None = None) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO usage_calls(ts, provider, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, conv_id, agent, ducky_label, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(entry.get(c) for c in COLUMNS),
        )
        if retention_s:
            conn.execute("DELETE FROM usage_calls WHERE ts < ?", (float(entry["ts"]) - retention_s,))


def since(ts: float) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT ts, provider, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
        "conv_id, agent, ducky_label, cost_usd FROM usage_calls WHERE ts >= ? ORDER BY ts",
        (ts,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("cost_usd") is None:
            d.pop("cost_usd", None)
        out.append(d)
    return out


def count() -> int:
    return int(db.connect().execute("SELECT count(*) FROM usage_calls").fetchone()[0])
