"""When a saved ask_user question is stale: its chat was deleted, or moved on.

A question keeps waiting across reloads and restarts (memory chat/durable-required-questions),
but not forever: once the chat has a newer user turn (the person or a lead wrote again, so the
agent moved past it) or the chat is gone, the card would only come back on every start.
Stop on a chat drops its questions directly (``ui_rpc.drop_for_conv``).
"""

from __future__ import annotations

from typing import Any


def _seconds(ts: float) -> float:
    return ts / 1000.0 if ts > 1e11 else ts  # panel rows may carry Date.now() milliseconds


def is_stale(conv_id: str, asked_at: float) -> bool:
    """True when the question asked in ``conv_id`` at ``asked_at`` (wall clock) is past.

    Local reads only (chats store). Any failure keeps the question: never drop on doubt.
    """
    if not conv_id:
        return False
    try:
        from backend.store import db
        from backend.store.repos import chats

        conn = db.connect()
        if chats._was_deleted(conn, conv_id):
            return True
        row = conn.execute(
            "SELECT max(ts) FROM messages WHERE conv_id=? AND role='user'", (conv_id,)
        ).fetchone()
    except Exception:  # noqa: BLE001 - an unreadable store keeps the question
        return False
    newest = _seconds(float((row or [0])[0] or 0.0))
    return newest > float(asked_at or 0.0)


def stale_ids(slots: dict[str, Any]) -> list[str]:
    """Request ids of open ask_user slots that are stale."""
    return [rid for rid, slot in slots.items()
            if slot.method == "ask_user" and not slot.event.is_set() and is_stale(slot.conv_id, slot.asked_at)]
