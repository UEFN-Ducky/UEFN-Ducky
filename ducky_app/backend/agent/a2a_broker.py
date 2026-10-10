"""Agent-to-agent broker: per-chat inboxes, reply threads, inactivity notices.

Traycer-style fire-and-forget messaging between panel chats (embedded duckies
AND external coding agents — Claude Code / Codex / Cursor conversations):

  - ``send()`` enqueues an envelope on the receiver's inbox and returns
    immediately. With ``expect_reply`` it mints a ``response_id`` thread the
    receiver must echo back on its reply.
  - Delivery: an idle receiver is woken with a formatted turn (embedded ducky =
    normal run; external agent = session resume). A busy receiver keeps the
    envelope queued; the queue drains when its turn ends.
  - ``on_agent_stopped()`` (wired into agent_modes) drains queues and fires
    inactivity notices to senders whose threads the stopped agent still owes.

Everything is RAM-only, mirroring Traycer's broker; chats persist their own
messages so nothing here needs disk.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from backend.agent.a2a_format import format_agent_message, format_agent_notice

_RING_MAX = 20
_QUIET_SWEEP_S = 300.0

_lock = threading.RLock()


@dataclass
class Envelope:
    sender_conv_id: str
    receiver_conv_id: str
    body: str
    response_id: str = ""
    is_notice: bool = False
    enqueued_at: float = field(default_factory=time.time)


@dataclass
class Thread:
    response_id: str
    sender_conv_id: str
    receiver_conv_id: str
    created_at: float = field(default_factory=time.time)
    noticed: bool = False
    deliver_result: bool = False
    """Spawn threads: when the receiver's turn ends cleanly, auto-deliver its
    last assistant text back to the sender instead of a turn-ended notice."""


_inbox: dict[str, deque[Envelope]] = {}
_ring: dict[str, list[Envelope]] = {}
_threads: dict[str, Thread] = {}
# Receivers currently running a broker-delivered turn (so their agent_stopped
# drains the queue again but never fires a self-notice).
_delivering: set[str] = set()
_delivery_inflight: set[str] = set()
_delivery_again: set[str] = set()
_active: dict[str, dict[str, Any]] = {}
_held: dict[str, list[Envelope]] = {}
_cooldowns: dict[str, float] = {}
_stopped: set[str] = set()
_timers: dict[str, tuple[object, Any]] = {}
_completed_runs: set[str] = set()
_uncertain_chats: set[str] = set()


def _account_key(conv_id: str) -> str:
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(conv_id)
    if conv is None:
        return "chat:" + conv_id
    agent = str(getattr(conv, "coding_agent", "") or "ducky")
    # Outside CLIs currently expose one configured login per adapter, not a
    # stable account ID. Do not lock out unrelated adapters/providers.
    if agent != "ducky":
        return "cli:" + agent
    provider = str(getattr(conv, "provider", "") or "")
    return "provider:" + provider if provider else "chat:" + conv_id


def cooldown_remaining(conv_id: str, *, now: float | None = None) -> float:
    key = _account_key(conv_id)
    with _lock:
        return max(0.0, _cooldowns.get(key, 0.0) - (time.time() if now is None else now))


def automatic_work_blocked(conv_id: str, *, now: float | None = None, queued: bool = False) -> bool:
    remaining = cooldown_remaining(conv_id, now=now)
    with _lock:
        return bool(remaining or conv_id in _stopped or
                    (not queued and (_held.get(conv_id) or conv_id in _uncertain_chats)))


def resume_by_user(conv_id: str) -> None:
    """A user continuation clears Stop, never silently replays held envelopes."""
    with _lock:
        _stopped.discard(conv_id)
        _uncertain_chats.discard(conv_id)


def sweep_quiet_threads(max_age_s: float = _QUIET_SWEEP_S) -> int:
    """Drop abandoned reply-expected threads and empty inbox/ring keys."""
    now = time.time()
    removed = 0
    with _lock:
        protected = set(_active) | {c for c, rows in _held.items() if rows} | {c for c, rows in _inbox.items() if rows}
        stale = [rid for rid, t in _threads.items() if (now - t.created_at) > max_age_s
                 and t.receiver_conv_id not in protected
                 and not cooldown_remaining(t.receiver_conv_id, now=now)]
        for rid in stale:
            _threads.pop(rid, None)
            removed += 1
        live_convs = {t.sender_conv_id for t in _threads.values()} | {
            t.receiver_conv_id for t in _threads.values()
        } | _delivering
        for mapping in (_inbox, _ring):
            for cid in [c for c in mapping if c not in live_convs]:
                bucket = mapping.get(cid)
                if not bucket:
                    mapping.pop(cid, None)
    return removed


def _conv_meta(conv_id: str) -> tuple[str, str]:
    """(title-ish name, coding_agent) for labels; tolerant of missing chats."""
    try:
        from frontend.ui_web.project_chats import load_conversation

        conv = load_conversation(conv_id)
    except Exception:
        conv = None
    if conv is None:
        return "", ""
    name = (conv.ducky_name or conv.title or "").strip()
    return name, (conv.coding_agent or "ducky").strip()


def mint_response_id() -> str:
    return uuid.uuid4().hex[:12]


def open_thread(
    sender_conv_id: str,
    receiver_conv_id: str,
    response_id: str = "",
    *,
    deliver_result: bool = False,
) -> str:
    """Register a reply-expected thread; idempotent per sender→receiver pair."""
    with _lock:
        held_threads = {e.response_id for rows in _held.values() for e in rows if e.response_id}
        for t in _threads.values():
            if (t.sender_conv_id == sender_conv_id and t.receiver_conv_id == receiver_conv_id
                    and t.response_id not in held_threads):
                t.deliver_result = t.deliver_result or deliver_result
                return t.response_id
        rid = response_id or mint_response_id()
        _threads[rid] = Thread(rid, sender_conv_id, receiver_conv_id, deliver_result=deliver_result)
        return rid


def close_thread(response_id: str) -> Thread | None:
    with _lock:
        return _threads.pop(response_id, None)


def open_threads_for_receiver(receiver_conv_id: str) -> list[Thread]:
    with _lock:
        return [t for t in _threads.values() if t.receiver_conv_id == receiver_conv_id]


def read_inbox(conv_id: str) -> list[dict[str, Any]]:
    """Recent inbox, including originals held for manual recovery."""
    with _lock:
        items = list(_ring.get(conv_id, [])) + list(_inbox.get(conv_id, ()))
        known = {id(e) for e in items}
        items.extend(e for e in _held.get(conv_id, []) if id(e) not in known)
        held = {id(e) for e in _held.get(conv_id, [])}
    return [{"from": e.sender_conv_id, "response_id": e.response_id,
             "is_notice": e.is_notice, "enqueued_at": e.enqueued_at, "body": e.body,
             "status": "held_manual_recovery" if id(e) in held else "received"}
            for e in items]


def send(
    *,
    sender_conv_id: str,
    receiver_conv_id: str,
    body: str,
    expect_reply: bool,
    response_id: str = "",
) -> dict[str, Any]:
    """Fire-and-forget send. Returns {response_id} (empty when no reply expected).

    ``response_id`` set + ``expect_reply=False``  → this IS the reply: closes the
    thread and routes the body back to the thread's sender.
    """
    if expect_reply and response_id:
        raise ValueError("pass either expect_reply=true OR response_id (a reply), not both")

    closing: Thread | None = None
    if response_id and not expect_reply:
        closing = close_thread(response_id)

    rid = ""
    if expect_reply:
        rid = open_thread(sender_conv_id, receiver_conv_id, "")

    envelope = Envelope(
        sender_conv_id=sender_conv_id,
        receiver_conv_id=receiver_conv_id,
        body=body,
        response_id=rid if expect_reply else (response_id or ""),
    )
    with _lock:
        _inbox.setdefault(receiver_conv_id, deque()).append(envelope)
    _kick_delivery(receiver_conv_id)
    return {
        "response_id": rid,
        "closed_thread": bool(closing),
        "queued_for": receiver_conv_id,
    }


def send_notice(
    *,
    sender_conv_id: str,
    receiver_conv_id: str,
    body: str,
) -> None:
    envelope = Envelope(
        sender_conv_id=sender_conv_id,
        receiver_conv_id=receiver_conv_id,
        body=body,
        is_notice=True,
    )
    with _lock:
        _inbox.setdefault(receiver_conv_id, deque()).append(envelope)
    _kick_delivery(receiver_conv_id)


def leads_receiver(sender_conv_id: str, receiver_conv_id: str) -> bool:
    """True when the sender leads a group that holds the receiver, directly or through
    nested groups (a coordinator over group hubs). Authority only flows down: a member
    reporting to its leader is still fenced as data."""
    if not sender_conv_id or not receiver_conv_id or sender_conv_id == receiver_conv_id:
        return False
    try:
        import importlib

        from frontend.ui_web.group_orchestrator import group_members

        chats = importlib.import_module("frontend.ui_web.project_chats")
        # Every project: the team's chats stay in their project when the user opens another.
        if hasattr(chats, "iter_conversations_by_project"):
            convs = [conv for _, conv in chats.iter_conversations_by_project()]
        else:
            convs = list(chats.list_all_conversation_metadata())
        groups = [c for c in convs if getattr(c, "is_group", False)]
    except Exception:
        return False
    containing: dict[str, list[Any]] = {}
    for group in groups:
        for member in group_members(group):
            mid = str(member.get("member_conv_id") or "")
            if mid:
                containing.setdefault(mid, []).append(group)
    seen: set[str] = set()
    frontier = [receiver_conv_id]
    while frontier:
        for group in containing.get(frontier.pop(), []):
            if group.id in seen:
                continue
            seen.add(group.id)
            if (getattr(group, "leader_conv_id", "") or "") == sender_conv_id:
                return True
            frontier.append(group.id)  # this group may sit inside a parent group
    return False


def _format_envelope(envelope: Envelope) -> str:
    if envelope.is_notice:
        return envelope.body
    title, agent = _conv_meta(envelope.sender_conv_id)
    return format_agent_message(
        sender_conv_id=envelope.sender_conv_id,
        sender_title=title,
        sender_coding_agent=agent,
        body=envelope.body,
        response_id=envelope.response_id,
        from_leader=leads_receiver(envelope.sender_conv_id, envelope.receiver_conv_id),
    )


def _kick_delivery(conv_id: str) -> None:
    """Deliver once; cooldown queues stay owned by this existing RAM broker."""
    with _lock:
        if automatic_work_blocked(conv_id, queued=True):
            _retry_queue_later(conv_id)
            return
        if conv_id in _delivery_inflight:
            _delivery_again.add(conv_id)
            return
        if conv_id in _active:
            return
        _delivery_inflight.add(conv_id)

    def _worker() -> None:
        from frontend.ui_web.agent_modes import is_agent_running, run_message, wait_for_idle
        batch = []
        try:
            sweep_quiet_threads()
            if is_agent_running(conv_id):
                wait_for_idle(conv_id, 1.0)
                if is_agent_running(conv_id):
                    return
            with _lock:
                queue = _inbox.get(conv_id)
                if not queue or automatic_work_blocked(conv_id, queued=True):
                    return
                batch = list(queue)
                queue.clear()
                _active[conv_id] = {"envelopes": batch}
                _delivering.add(conv_id)
                ring = _ring.setdefault(conv_id, [])
                ring.extend(batch)
                del ring[:-_RING_MAX]
            # Once handed to run_message, an empty result or exception is NOT
            # proof of non-execution. Never automatically requeue this batch.
            started = run_message(conv_id, "\n\n".join(_format_envelope(e) for e in batch),
                                  "agent", "", queue_if_busy=True)
            if not started:
                raise RuntimeError("Delivery unconfirmed")
        except Exception:
            with _lock:
                active = _active.get(conv_id)
                if batch and active and active["envelopes"] is batch:
                    _held.setdefault(conv_id, []).extend(batch)
                    _active.pop(conv_id, None)
                    _delivering.discard(conv_id)
        finally:
            with _lock:
                _delivery_inflight.discard(conv_id)
                again = conv_id in _delivery_again and bool(_inbox.get(conv_id))
                _delivery_again.discard(conv_id)
            if again:
                _kick_delivery(conv_id)

    threading.Thread(target=_worker, daemon=True, name=f"a2a-deliver-{conv_id[:8]}").start()


def _last_assistant_text(conv_id: str) -> str:
    try:
        from frontend.ui_web.project_chats import load_conversation

        conv = load_conversation(conv_id)
    except Exception:
        conv = None
    if conv is None:
        return ""
    for message in reversed(conv.messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _failure_detail(conv_id: str, detail: str) -> str:
    """Attach partial assistant text so the Producer knows what finished before the crash."""
    parts: list[str] = []
    if (detail or "").strip():
        parts.append(detail.strip())
    partial = _last_assistant_text(conv_id)
    if partial:
        clip = partial if len(partial) <= 2000 else partial[:2000] + "…"
        parts.append(f"Partial progress before stop:\n{clip}")
    return "\n\n".join(parts)


_ESCALATE_DEPTH = 8


def _ancestor_leader_ids(conv_id: str) -> list[str]:
    """Walk parent_conv_id group hubs; collect each group's leader leaf chat id."""
    try:
        from frontend.ui_web.group_orchestrator import (
            group_leader_member,
            is_group_conversation,
        )
        from frontend.ui_web.project_chats import load_conversation
    except Exception:
        return []

    leaders: list[str] = []
    seen: set[str] = set()
    cur = (conv_id or "").strip()
    for _ in range(_ESCALATE_DEPTH):
        if not cur or cur in seen:
            break
        seen.add(cur)
        conv = load_conversation(cur)
        if conv is None:
            break
        parent_id = (getattr(conv, "parent_conv_id", None) or "").strip()
        if not parent_id or parent_id in seen:
            break
        parent = load_conversation(parent_id)
        if parent is None:
            break
        if is_group_conversation(parent):
            pick = group_leader_member(parent)
            lid = str(pick.get("member_conv_id") or "").strip() if pick else ""
            if not lid:
                lid = (getattr(parent, "leader_conv_id", None) or "").strip()
            if lid and lid != conv_id and lid not in leaders:
                leaders.append(lid)
        cur = parent_id
    return leaders


def _escalate_failure_to_group_leaders(
    conv_id: str,
    *,
    notice_reason: str,
    notice_detail: str,
    already_notified: set[str],
    response_id: str = "",
) -> None:
    """Wake ancestor group leaders on timeout/error (Producer / Hub lead), deduped."""
    if notice_reason not in ("timed-out", "errored"):
        return
    title, agent = _conv_meta(conv_id)
    body = format_agent_notice(
        receiver_conv_id=conv_id,
        receiver_title=title,
        receiver_coding_agent=agent,
        response_id=response_id or "escalation",
        reason=notice_reason,
        detail=notice_detail,
    )
    for leader_id in _ancestor_leader_ids(conv_id):
        if not leader_id or leader_id == conv_id or leader_id in already_notified:
            continue
        already_notified.add(leader_id)
        send_notice(sender_conv_id=conv_id, receiver_conv_id=leader_id, body=body)


_ACCOUNT_LIMIT_CODES = {"rate_limit_error", "rate_limit_exceeded", "insufficient_quota"}
_LIMIT_RETRY_S = 900.0


def account_limited(text: str) -> bool:
    """Classify a CURRENT terminal diagnostic, never conversation history.

    Classification does not establish that execution never happened.
    """
    value = (text or "").strip()
    try:
        obj = json.loads(value)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        error = obj.get("error", obj)
        if isinstance(error, dict):
            if error.get("type") in _ACCOUNT_LIMIT_CODES or error.get("code") in _ACCOUNT_LIMIT_CODES:
                return True
            value = str(error.get("message") or "")
    low = value.lower().replace("’", "'").strip()
    if low in _ACCOUNT_LIMIT_CODES or low in {"rate limit exceeded", "quota exceeded", "usage limit reached", "out of credits"}:
        return True
    return bool(re.fullmatch(r"you(?:'ve| have) hit your usage limit[.!]?(?:\s+(?:visit https://\S+ to purchase more credits|try again(?: at| in)? .+))?", low))


def _retry_queue_later(conv_id: str) -> None:
    """Deliver this chat's queued messages after a pause instead of failing them now."""
    with _lock:
        if conv_id in _timers or conv_id in _stopped:
            return
        delay = cooldown_remaining(conv_id)
        if delay <= 0:
            return
        token = object()

        def retry(cid: str) -> None:
            with _lock:
                current = _timers.get(cid)
                if current is None or current[0] is not token:
                    return
                _timers.pop(cid, None)
            if cooldown_remaining(cid):
                _retry_queue_later(cid)
                return
            _kick_delivery(cid)
            from frontend.ui_web import agent_modes
            drain = getattr(agent_modes, "_deliver_pending_agent_messages", None)
            if drain is not None and not automatic_work_blocked(cid, queued=True):
                drain(cid)

        timer = threading.Timer(delay, retry, args=(conv_id,))
        timer.daemon = True
        _timers[conv_id] = (token, timer)
    timer.start()


def on_agent_stopped(conv_id: str, reason: str, *, detail: str = "", run_id: str = "") -> None:
    """Turn-lifecycle hook: drain this chat's queue + notify owed senders."""
    was_delivery = False
    with _lock:
        if run_id:
            if run_id in _completed_runs:
                return
            _completed_runs.add(run_id)
        was_delivery = conv_id in _delivering
        _delivering.discard(conv_id)
        active = _active.pop(conv_id, None)
        if reason == "error" and account_limited(detail):
            _uncertain_chats.add(conv_id)
            key = _account_key(conv_id)
            if _cooldowns.get(key, 0) <= time.time():
                _cooldowns[key] = time.time() + _LIMIT_RETRY_S
            if active:
                _held.setdefault(conv_id, []).extend(active["envelopes"])
            _retry_queue_later(conv_id)
            return

    with _lock:
        held_threads = {e.response_id for e in _held.get(conv_id, []) if e.response_id}
    owed = [t for t in open_threads_for_receiver(conv_id) if t.response_id not in held_threads]

    # Spawn threads: a clean turn end IS the reply — hand the sender the
    # receiver's answer instead of a turn-ended notice.
    if reason == "done":
        for thread in [t for t in owed if t.deliver_result]:
            close_thread(thread.response_id)
            answer = _last_assistant_text(conv_id) or "(the agent finished without a reply)"
            envelope = Envelope(
                sender_conv_id=conv_id,
                receiver_conv_id=thread.sender_conv_id,
                body=f"Result for your request (response_id {thread.response_id}):\n\n{answer}",
                response_id="",
            )
            with _lock:
                _inbox.setdefault(thread.sender_conv_id, deque()).append(envelope)
            _kick_delivery(thread.sender_conv_id)
        owed = [t for t in owed if not t.deliver_result]

    # Owed replies → inactivity notices (Traycer reason vocabulary).
    # timeout must NOT map to "quiet" — quiet means "may still be working".
    notice_reason = {
        "done": "turn-ended",
        "cancelled": "user-stopped",
        "timeout": "timed-out",
        "needs_login": "awaiting-input",
    }.get(reason, "errored" if reason == "error" else reason)
    notice_detail = detail
    if notice_reason in ("timed-out", "errored", "user-stopped"):
        notice_detail = _failure_detail(conv_id, detail)

    already_notified: set[str] = set()
    response_id_for_escalate = ""
    if owed:
        title, agent = _conv_meta(conv_id)
        for thread in owed:
            if thread.noticed and notice_reason == "turn-ended":
                continue
            thread.noticed = True
            if not response_id_for_escalate:
                response_id_for_escalate = thread.response_id
            body = format_agent_notice(
                receiver_conv_id=conv_id,
                receiver_title=title,
                receiver_coding_agent=agent,
                response_id=thread.response_id,
                reason=notice_reason,
                detail=notice_detail,
            )
            already_notified.add(thread.sender_conv_id)
            send_notice(
                sender_conv_id=conv_id,
                receiver_conv_id=thread.sender_conv_id,
                body=body,
            )

    # Timeout/crash: always escalate up the group tree (Hub → Producer), even
    # when nobody had an open expect_reply thread.
    if reason in ("timeout", "error"):
        _escalate_failure_to_group_leaders(
            conv_id,
            notice_reason=notice_reason,
            notice_detail=notice_detail,
            already_notified=already_notified,
            response_id=response_id_for_escalate,
        )

    # Anything queued while it was busy → deliver now.
    with _lock:
        has_queue = bool(_inbox.get(conv_id))
    if has_queue:
        _kick_delivery(conv_id)
    # Keep linters honest: was_delivery reserved for future self-notice gating.
    _ = was_delivery


def on_agent_cancelled_by_user(conv_id: str) -> None:
    """User stopped this chat outright: drop undelivered envelopes, close threads
    the stopped chat owed, and tell each sender (receiver-cancelled)."""
    with _lock:
        _stopped.add(conv_id)
        _inbox.pop(conv_id, None)
        _held.pop(conv_id, None)
        _uncertain_chats.discard(conv_id)
        _active.pop(conv_id, None)
        _delivering.discard(conv_id)
        timer = _timers.pop(conv_id, None)
        if timer is not None:
            timer[1].cancel()
    title, agent = _conv_meta(conv_id)
    for thread in open_threads_for_receiver(conv_id):
        close_thread(thread.response_id)
        body = format_agent_notice(
            receiver_conv_id=conv_id,
            receiver_title=title,
            receiver_coding_agent=agent,
            response_id=thread.response_id,
            reason="receiver-cancelled",
        )
        send_notice(sender_conv_id=conv_id, receiver_conv_id=thread.sender_conv_id, body=body)


def stats() -> dict[str, Any]:
    with _lock:
        return {
            "held": {k: len(v) for k, v in _held.items() if v},
            "stopped": sorted(_stopped),
            "cooldowns": dict(_cooldowns),
            "queued": {k: len(v) for k, v in _inbox.items() if v},
            "open_threads": [
                {
                    "response_id": t.response_id,
                    "sender": t.sender_conv_id,
                    "receiver": t.receiver_conv_id,
                    "age_s": round(time.time() - t.created_at, 1),
                }
                for t in _threads.values()
            ],
        }
