"""Request/response broker between UI-driving tools and the React panel.

Fire-and-forget panel events already flow one way (tool → panel) through
``/__panel_event``. Navigating, enumerating clickable targets, and waiting for
the user to click a spotlighted control all need the *answer* to come back, so
this broker adds the missing direction.

Flow (all inside the panel process, driven over loopback HTTP):

1. A caller POSTs ``/__panel_rpc`` → :func:`submit` registers a pending slot and
   the handler pushes the returned ``ui_rpc_request`` event to the React panel.
2. React does the work (navigate, read the target registry, render a spotlight,
   optionally wait for a real user click) and answers via
   ``PanelApi.ui_rpc_respond(request_id, payload)`` → :func:`respond`.
3. :func:`respond` stores the result and wakes the waiter; the HTTP handler
   returns the payload to the caller.

The caller may be the stdio MCP bridge (a separate process) or an embedded
agent in the panel process — both reach this broker the same way, over loopback.

Several windows can be open (popped-out desktop windows, the phone panel). Requests
that show something (Show me, tours, navigate) go to the window the user last clicked
or typed in: it claims "active" through ``PanelApi.ui_rpc_active`` and acknowledges a
request it takes with ``ui_rpc_ack``. When it doesn't acknowledge in time (closed, gone
to sleep) — or no window is known yet — the request goes to every window and the first
one to ``ui_rpc_claim`` it runs it; the others drop it, so nothing plays twice.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

# Ordinary UI requests expire when their caller vanishes. Questions are never
# swept: a transport disconnect must not remove the question or release its gate.
_MAX_TTL_S = 15 * 60.0


# Methods that show something on screen: only the active window answers them.
WINDOW_METHODS = frozenset({"show", "walkthrough_run", "tour_workflow", "navigate", "list_targets"})


class _Pending:
    __slots__ = ("event", "acked", "result", "created", "conv_id", "method", "params")

    def __init__(self, conv_id: str = "", method: str = "", params: dict | None = None) -> None:
        self.event = threading.Event()
        self.acked = threading.Event()
        self.result: dict[str, Any] | None = None
        self.created = time.monotonic()
        self.conv_id = conv_id
        self.method = method
        self.params = dict(params or {})


_pending: dict[str, _Pending] = {}
_lock = threading.Lock()
_active_client = ""
_restored_store = ""


def _restore_questions_locked() -> None:
    """Load unanswered questions once per database; failures keep callers blocked."""
    global _restored_store
    from backend.store import db
    from backend.store.repos import kv

    key = str(db.db_path())
    if key == _restored_store:
        return
    saved = json.loads(kv.meta_get("ui_rpc:questions") or "{}")
    restored = {}
    for rid, params in saved.items():
        restored[rid] = _Pending(str(params.get("conv_id") or ""), "ask_user", params)
    _pending.clear()
    _pending.update(restored)
    _restored_store = key


def _save_questions_locked(exclude: str = "") -> None:
    from backend.store.repos import kv

    saved = {rid: slot.params for rid, slot in _pending.items()
             if rid != exclude and slot.method == "ask_user" and not slot.event.is_set()}
    kv.meta_set("ui_rpc:questions", json.dumps(saved, ensure_ascii=False))


def mark_active(client_id: str, active: bool = True) -> None:
    """A window says the user is using it (or, closing, that it is gone)."""
    global _active_client
    cid = (client_id or "").strip()
    with _lock:
        if active and cid:
            _active_client = cid
        elif not active and cid and _active_client == cid:
            _active_client = ""


def window_for(method: str) -> str:
    """The window that should answer ``method`` ("" = any)."""
    with _lock:
        return _active_client if method in WINDOW_METHODS else ""


def ack(request_id: str) -> bool:
    """A window took the request (it will answer when done)."""
    with _lock:
        slot = _pending.get(request_id)
    if slot is None:
        return False
    slot.acked.set()
    return True


def claim(request_id: str) -> bool:
    """First window to claim a request sent to every window runs it (True); later ones get False."""
    with _lock:
        slot = _pending.get(request_id)
        if slot is None or slot.acked.is_set() or slot.event.is_set():
            return False
        slot.acked.set()
        return True


def wait_ack(request_id: str, timeout: float) -> bool:
    """True when a window took the request (or already answered) within ``timeout``."""
    with _lock:
        slot = _pending.get(request_id)
    if slot is None:
        return True  # answered and collected already
    end = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < end:
        if slot.acked.is_set() or slot.event.is_set():
            return True
        slot.acked.wait(0.05)
    return slot.acked.is_set() or slot.event.is_set()


def _sweep_locked() -> None:
    cutoff = time.monotonic() - _MAX_TTL_S
    stale = [rid for rid, slot in _pending.items() if slot.created < cutoff and slot.method != "ask_user"]
    for rid in stale:
        _pending.pop(rid, None)


def submit(method: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Register a pending request; return ``(request_id, event_payload)``.

    The caller is responsible for pushing ``event_payload`` (a ``ui_rpc_request``
    event) to the panel and then calling :func:`wait`.
    """
    request_id = uuid.uuid4().hex
    conv_id = str((params or {}).get("conv_id") or "").strip()
    with _lock:
        _restore_questions_locked()
        _sweep_locked()
        _pending[request_id] = _Pending(conv_id, method, params)
        if method == "ask_user":
            _save_questions_locked()
    payload = {
        "type": "ui_rpc_request",
        "request_id": request_id,
        "method": str(method),
        "params": dict(params or {}),
    }
    return request_id, payload


def wait(request_id: str, timeout: float) -> dict[str, Any] | None:
    """Block up to ``timeout`` seconds for a response.

    Returns the response dict once answered, ``{"error": ...}`` for an unknown
    id, or ``None`` if still pending (caller may re-poll).
    """
    with _lock:
        slot = _pending.get(request_id)
        if slot is not None:
            slot.created = time.monotonic()  # keep-alive: sweep idles, not active polls
    if slot is None:
        return {"error": "unknown request_id"}
    if slot.event.wait(max(0.0, float(timeout))):
        with _lock:
            _pending.pop(request_id, None)
        return slot.result if slot.result is not None else {}
    return None


def respond(request_id: str, payload: dict[str, Any]) -> bool:
    """Deliver the panel's answer. Returns ``False`` if the id is unknown/expired."""
    with _lock:
        _restore_questions_locked()
        slot = _pending.get(request_id)
        if slot is None or slot.event.is_set():
            return False  # first answer wins: another window's late close can't overwrite it
        if slot.method == "ask_user":
            answers = (payload or {}).get("answers")
            # Window shutdowns, transport errors and empty responses are not answers.
            if not (payload or {}).get("ok") or not isinstance(answers, dict) or not answers:
                return False
            for question in slot.params.get("questions") or []:
                answer = answers.get(question.get("id"))
                if not isinstance(answer, dict):
                    return False
                if question.get("required", True) and (
                    answer.get("skipped") or not (answer.get("selected") or str(answer.get("text") or "").strip())
                ):
                    return False
        if slot.method == "ask_user":
            # Commit before waking the caller; a failed save never grants approval.
            _save_questions_locked(exclude=request_id)
        slot.result = dict(payload or {})
        slot.event.set()
    return True


def cancel(request_id: str) -> None:
    """Drop ordinary requests; unanswered questions survive caller cancellation."""
    with _lock:
        slot = _pending.get(request_id)
        if slot is not None and slot.method == "ask_user" and not slot.event.is_set():
            return
        _pending.pop(request_id, None)


def wait_for_answers(conv_id: str, cancel_event: Any | None = None) -> None:
    """Suspend continuation until every question in this chat has a real answer."""
    while True:
        with _lock:
            _restore_questions_locked()
            slots = [slot for slot in _pending.values()
                     if slot.conv_id == conv_id and slot.method == "ask_user" and not slot.event.is_set()]
        if not slots:
            return
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Stopped while waiting for the user's answer.")
        slots[0].event.wait(0.2)


def has_pending_for_conv(conv_id: str) -> bool:
    """True while an unanswered ui_rpc (e.g. ask_user) is bound to this chat."""
    cid = (conv_id or "").strip()
    if not cid:
        return False
    with _lock:
        _restore_questions_locked()
        return any(slot.conv_id == cid and not slot.event.is_set() for slot in _pending.values())



def question_gate(conv_id: str, timeout: float) -> bool:
    """Wait one bounded HTTP round; True means this chat still needs an answer."""
    with _lock:
        _restore_questions_locked()
        waiting = [slot for slot in _pending.values()
                   if slot.conv_id == conv_id and slot.method == "ask_user" and not slot.event.is_set()]
    if waiting:
        waiting[0].event.wait(timeout)
    with _lock:
        return any(slot.conv_id == conv_id and slot.method == "ask_user" and not slot.event.is_set()
                   for slot in _pending.values())


def pending_questions() -> list[dict[str, Any]]:
    """Replay unanswered questions when a panel reconnects or reloads."""
    with _lock:
        _restore_questions_locked()
        return [
            {"type": "ui_rpc_request", "request_id": rid, "method": slot.method, "params": dict(slot.params)}
            for rid, slot in _pending.items()
            if slot.method == "ask_user" and not slot.event.is_set()
        ]
