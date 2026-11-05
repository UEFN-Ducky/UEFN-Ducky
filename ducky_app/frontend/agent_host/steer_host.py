"""Host side of D20: the steer and question checks of a running chat are answered by its agent host.

Every Ducky tool call asks ``question_gate`` before it runs and ``steer_take`` / ``steer_ack``
after; while a chat's turn runs in a host, its tool server asks that host over the host's own
pipe (``backend/panel/rpc.host_rpc``) instead of the app, so a busy window never slows tools.

- The host's leased inbox is ``frontend.ui_web.steer`` itself, in this process: the app
  forwards the person's posts (``steer_post`` frame → :func:`post`); ``take`` / ``ack`` are the
  same code the app runs for chats with no host.
- Question state: the app keeps the questions (the window answers them) and sends
  ``question_answer`` frames with ``open`` for host-owned chats; :func:`question_gate` waits on it.
- :func:`address` is the pipe and key the runner puts in the ``uefn`` server's env
  (``DUCKY_AGENT_HOST_PIPE`` / ``DUCKY_AGENT_HOST_KEY``) when it writes the ``--mcp-config``.

One request per connection: ``{"method": "steer_take", "params": {"conv_id": "c1"}}`` gets one
reply, e.g. ``{"lease_id": "…", "texts": ["use blue"], "steer_ids": ["s1"]}``.

``{"method": "memory_report", "params": {"top": 20}}`` answers what this host process holds
(``frontend.memory_report.report``): ``ducky_memory_report`` only measures the app window.
"""

from __future__ import annotations

import json
import os
import threading
from multiprocessing.connection import Listener
from typing import Any

GATE_WAIT_S = 25.0  # one question_gate round; the tool server asks again while blocked

_lock = threading.Lock()
_changed = threading.Condition(_lock)
_open_questions: set[str] = set()
_address: tuple[str, str] | None = None
_in_host = False


def enable() -> None:
    """This process is an agent host (turns.on_frame calls it): :func:`address` may serve."""
    global _in_host
    _in_host = True


def set_question(conv_id: str, open_: bool) -> None:
    """The app's word on whether this chat has an unanswered question."""
    with _changed:
        if open_:
            _open_questions.add(conv_id)
        else:
            _open_questions.discard(conv_id)
        _changed.notify_all()


def question_gate(conv_id: str, timeout: float = GATE_WAIT_S) -> bool:
    """Wait one round for an open question to be answered; True means still blocked."""
    with _changed:
        _changed.wait_for(lambda: conv_id not in _open_questions, timeout)
        return conv_id in _open_questions


def post(conv_id: str, steer_id: str, text: str) -> None:
    """A person's message for the chat's turn here (empty text: taken back)."""
    from frontend.ui_web import steer

    if str(text or "").strip():
        steer.hold(conv_id, steer_id, text)
    else:
        steer.post(conv_id, steer_id, "")


def answer(method: str, params: dict[str, Any]) -> dict[str, Any]:
    from frontend.ui_web import steer

    conv_id = str(params.get("conv_id") or "")
    if method == "steer_take":
        return steer.take(conv_id)
    if method == "steer_ack":
        steer.ack(conv_id, str(params.get("lease_id") or ""))
        return {"ok": True}
    if method == "steer_pending":
        return {"pending": steer.has_queued(conv_id)}
    if method == "question_gate":
        return {"blocked": question_gate(conv_id, GATE_WAIT_S)}
    if method == "memory_report":
        return _memory_report(params)
    return {"error": f"unknown method {method!r}"}


def _memory_report(params: dict[str, Any]) -> dict[str, Any]:
    from frontend import memory_report

    try:
        return memory_report.report(
            start_trace=bool(params.get("start_trace")), stop_trace=bool(params.get("stop_trace")),
            collect=bool(params.get("collect", True)), top=int(params.get("top") or memory_report.TOP),
            walk_budget_s=float(params.get("walk_budget_s") or memory_report.WALK_BUDGET_S),
        )
    except Exception as exc:  # noqa: BLE001 - the caller hears why
        return {"error": f"{type(exc).__name__}: {exc}"}


def _serve_one(conn: Any) -> None:
    try:
        request = json.loads(conn.recv_bytes())
        reply = answer(str(request.get("method") or ""), dict(request.get("params") or {}))
        conn.send_bytes(json.dumps(reply).encode("utf-8"))
    except Exception:  # noqa: BLE001 - the caller gave up or sent junk; it falls back to the app
        pass
    finally:
        conn.close()


def _accept_loop(listener: Listener) -> None:
    while True:
        try:
            conn = listener.accept()
        except Exception:  # noqa: BLE001 - a client that failed the key check; keep serving
            continue
        threading.Thread(target=_serve_one, args=(conn,), name="host-steer-rpc", daemon=True).start()


def address() -> tuple[str, str] | None:
    """(pipe, key hex) of this host's steer pipe, started on first use; None outside a host."""
    global _address
    from frontend.agent_host import protocol

    if not _in_host:
        return None
    with _lock:
        if _address is None:
            key = os.urandom(32)
            tag = f"rpc-{os.urandom(4).hex()}"
            listener = Listener(protocol.pipe_name(tag), authkey=key)
            threading.Thread(target=_accept_loop, args=(listener,), name="host-steer-accept", daemon=True).start()
            _address = (protocol.pipe_name(tag), key.hex())
        return _address


def mcp_env() -> dict[str, str]:
    """The env the ``uefn`` server needs to ask this host; {} outside a host."""
    from backend.panel.rpc import HOST_KEY_ENV, HOST_PIPE_ENV

    addr = address()
    return {HOST_PIPE_ENV: addr[0], HOST_KEY_ENV: addr[1]} if addr else {}
