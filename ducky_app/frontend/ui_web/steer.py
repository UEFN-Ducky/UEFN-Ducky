"""A message the person sends into a running turn ("Send now").

It waits here until the agent's next Ducky tool call, which hands it over with that
tool's result (backend/server.py), so the turn takes it in and keeps going instead of
being stopped and re-run. Whatever no tool call picked up is dropped when the turn ends:
the composer still holds it in its queue and sends it as the next turn.

A tool call leases the messages (``take``) and confirms once it attached them (``ack``);
a lease nobody confirms within ``LEASE_S`` puts them back in the queue, so a tool server
that gave up waiting never loses what the person wrote.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

PushFn = Callable[[dict[str, Any]], None]

LEASE_S = 12.0  # just above the 10 s steer_take timeout

_lock = threading.Lock()
# Each item: {"id", "text", "lease": lease id or "" while queued, "at": lease time}.
_inbox: dict[str, list[dict[str, Any]]] = {}
_run_push: dict[str, PushFn] = {}
# App side, per chat whose turn runs in an agent host: {steer id: text} sent there, not attached yet.
_forwarded: dict[str, dict[str, str]] = {}
# Per chat, the steer ids a turn took in (newest last): a queued prompt the turn already
# showed must not run again as the next turn (Stop right after a tool call took it).
_delivered: dict[str, list[str]] = {}
KEEP_DELIVERED = 64  # ponytail: ids are unique per prompt; a bounded tail is plenty
_now = time.monotonic


def _mark_delivered(conv_id: str, steer_id: str) -> None:
    """Call under ``_lock``."""
    if not steer_id:
        return
    ids = _delivered.setdefault(conv_id, [])
    if steer_id not in ids:
        ids.append(steer_id)
        del ids[:-KEEP_DELIVERED]


def was_delivered(conv_id: str, steer_id: str) -> bool:
    """Whether a turn took this queued message in (it shows there; never send it again)."""
    with _lock:
        return str(steer_id or "").strip() in _delivered.get(str(conv_id or "").strip(), [])


def _expire(box: list[dict[str, Any]]) -> None:
    """Return unconfirmed leases to the queue. Call under ``_lock``."""
    # ponytail: lazy on every take/post/has_queued instead of a 2 s timer; nothing else reads the box.
    now = _now()
    for m in box:
        if m["lease"] and now - m["at"] >= LEASE_S:
            m["lease"] = ""


def post(conv_id: str, steer_id: str, text: str) -> bool:
    """Hold ``text`` for the chat's running turn. False when no turn runs (send it normally)."""
    from frontend.ui_web.agent_turns.state import is_agent_running

    cid = str(conv_id or "").strip()
    body = str(text or "").strip()
    sid = str(steer_id or "").strip()
    host = _host_of(cid)
    if not body:  # taken back out of the queue (edited or deleted), leased copies too
        with _lock:
            if cid in _inbox:
                _inbox[cid] = [m for m in _inbox[cid] if m["id"] != sid]
            _forwarded.get(cid, {}).pop(sid, None)
        if host is not None:
            host.send("steer_post", conv_id=cid, steer_id=sid, text="")
        return False
    if not cid or not sid or not is_agent_running(cid):
        return False
    if host is not None and host.send("steer_post", conv_id=cid, steer_id=sid, text=body):
        # The chat's turn runs in an agent host, whose tool server asks that host (D20).
        with _lock:
            _forwarded.setdefault(cid, {})[sid] = body
        return True
    hold(cid, sid, body)
    return True


def hold(conv_id: str, steer_id: str, text: str) -> None:
    """Queue the message in this process's inbox (the agent host calls it for its turns)."""
    with _lock:
        box = _inbox.setdefault(conv_id, [])
        _expire(box)
        if not any(m["id"] == steer_id for m in box):
            box.append({"id": steer_id, "text": str(text).strip(), "lease": "", "at": 0.0})


def _host_of(conv_id: str) -> Any:
    from frontend.agent_host import turns

    return turns.owns(conv_id)


def delivered(conv_id: str, steer_id: str) -> None:
    """The host attached a forwarded message (its ``user_steered`` event reached the app)."""
    with _lock:
        _forwarded.get(conv_id, {}).pop(steer_id, None)
        _mark_delivered(conv_id, steer_id)


def host_turn_ended(conv_id: str, *, reclaim: bool = False) -> None:
    """Forget what went to the chat's host; ``reclaim`` (the host died) queues it here first."""
    with _lock:
        items = _forwarded.pop(conv_id, {})
    if reclaim:
        for sid, text in items.items():
            hold(conv_id, sid, text)


def has_queued(conv_id: str) -> bool:
    """Whether the person wrote something no tool call holds yet (cheap: long tools poll it)."""
    with _lock:
        if _forwarded.get(conv_id):
            return True  # ponytail: sent to the host, not attached yet; host leases look queued
        box = _inbox.get(conv_id)
        if not box:
            return False
        _expire(box)
        return any(not m["lease"] for m in box)


def take(conv_id: str) -> dict[str, Any]:
    """Lease the chat's held messages, oldest first: ``{lease_id, texts, steer_ids}``.

    Nothing while an earlier lease is still open, so a message never rides on two tool results.
    """
    cid = str(conv_id or "").strip()
    lease = {"lease_id": "", "texts": [], "steer_ids": []}
    with _lock:
        box = _inbox.get(cid) or []
        _expire(box)
        if not box or any(m["lease"] for m in box):
            return lease
        lease["lease_id"] = uuid.uuid4().hex
        at = _now()
        for m in box:
            m["lease"], m["at"] = lease["lease_id"], at
            lease["texts"].append(m["text"])
            lease["steer_ids"].append(m["id"])
    return lease


def ack(conv_id: str, lease_id: str) -> list[str]:
    """The tool call attached the leased messages: drop them; each shows as the person's bubble."""
    cid = str(conv_id or "").strip()
    lid = str(lease_id or "").strip()
    if not lid:
        return []
    with _lock:
        box = _inbox.get(cid) or []
        done = [m for m in box if m["lease"] == lid]
        if done:
            _inbox[cid] = [m for m in box if m["lease"] != lid]
        for m in done:
            _mark_delivered(cid, m["id"])
        push = _run_push.get(cid)
    if not done:
        return []
    if push is None:
        from frontend.ui_web.agent_turns.push import get_panel_push

        push = get_panel_push()
    for m in done:
        if push is not None:
            push({"type": "user_steered", "conv_id": cid, "steer_id": m["id"], "text": m["text"]})
    return [m["text"] for m in done]


def bind_run(conv_id: str, push: PushFn) -> None:
    """The running turn's own push, so its checkpoint keeps the message in the saved turn."""
    with _lock:
        _run_push[conv_id] = push


def end_run(conv_id: str) -> None:
    with _lock:
        _inbox.pop(conv_id, None)
        _run_push.pop(conv_id, None)
