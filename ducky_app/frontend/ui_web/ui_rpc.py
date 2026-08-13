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
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

# A slot is swept only after nobody has polled it for this long, so a caller
# that vanished (process killed mid-request) cannot leak slots forever.
# Actively-polled waits (e.g. ducky_ask_user, which never times out) refresh
# the timestamp on every poll and live until answered.
_MAX_TTL_S = 15 * 60.0


class _Pending:
    __slots__ = ("event", "result", "created", "conv_id")

    def __init__(self, conv_id: str = "") -> None:
        self.event = threading.Event()
        self.result: dict[str, Any] | None = None
        self.created = time.monotonic()
        self.conv_id = conv_id


_pending: dict[str, _Pending] = {}
_lock = threading.Lock()


def _sweep_locked() -> None:
    cutoff = time.monotonic() - _MAX_TTL_S
    stale = [rid for rid, slot in _pending.items() if slot.created < cutoff]
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
        _sweep_locked()
        _pending[request_id] = _Pending(conv_id)
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
        slot = _pending.get(request_id)
    if slot is None:
        return False
    slot.result = dict(payload or {})
    slot.event.set()
    return True


def cancel(request_id: str) -> None:
    """Drop a pending slot (e.g. no panel available to answer it)."""
    with _lock:
        _pending.pop(request_id, None)


def has_pending_for_conv(conv_id: str) -> bool:
    """True while an unanswered ui_rpc (e.g. ask_user) is bound to this chat."""
    cid = (conv_id or "").strip()
    if not cid:
        return False
    with _lock:
        return any(slot.conv_id == cid for slot in _pending.values())
