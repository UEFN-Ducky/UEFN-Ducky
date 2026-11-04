"""Client for the panel's UI request/response broker (see ui_rpc.py).

UI-driving MCP tools call :func:`panel_rpc` to ask the panel to navigate,
enumerate clickable targets, or spotlight a control and (optionally) wait for
the user to click it. It posts to the panel's loopback control port.

When no panel is open the connection is refused and this returns
``{"error": "panel not open"}`` instead of hanging, so the tools degrade
cleanly in a headless bridge.

Long waits (``require_click``) never hold a single HTTP request open for
minutes: the panel handler answers within ``_HANDLER_WAIT_S`` or replies
``{"pending": true, request_id}``, and this client re-polls with ``GET`` until
the panel answers or the overall ``timeout`` budget runs out.
"""

from __future__ import annotations

import contextvars
import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from frontend.settings import PANEL_LISTENER_PORT

# Same loopback control port the event/run bridges already use (panel UI HTTP).
_RPC_URL = f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_rpc"

# Must exceed panel_httpd._RPC_HANDLER_WAIT_S (20s) so {pending} returns first.
_ROUND_TIMEOUT_S = 45.0


class _Unreachable(Exception):
    """Panel control port refused, or a poll socket timed out (``timed_out``)."""

    def __init__(self, message: str = "", *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def _http(method: str, url: str, body: bytes | None, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        timed_out = isinstance(reason, (TimeoutError, socket.timeout))
        raise _Unreachable(str(exc), timed_out=timed_out) from exc
    except (TimeoutError, OSError) as exc:
        timed_out = isinstance(exc, (TimeoutError, socket.timeout))
        raise _Unreachable(str(exc), timed_out=timed_out) from exc
    return json.loads(raw) if raw else {}


# A tool call's "what am I waiting on" record (shared_mcp's stuck report reads it): the
# panel method this call's thread is waiting for, and its pending request id.
_WAITING: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("panel_rpc_waiting", default=None)


def track_waits(holder: dict[str, Any]) -> contextvars.Token:
    """Record this context's panel waits in ``holder`` (threads and tasks it starts share it)."""
    return _WAITING.set(holder)


def untrack_waits(token: contextvars.Token) -> None:
    _WAITING.reset(token)


def panel_rpc(method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
    """Invoke a panel UI method and return its result dict.

    ``timeout`` is the overall budget in seconds. On success returns the panel's
    result payload; on failure returns a dict with an ``error`` key
    (``"panel not open"`` or ``"timeout"``) — callers should surface it rather
    than raise.
    """
    holder = _WAITING.get()
    if holder is None:
        return _panel_rpc(method, params, timeout, {})
    holder["panel_rpc"] = str(method)
    try:
        return _panel_rpc(method, params, timeout, holder)
    finally:
        holder.pop("panel_rpc", None)
        holder.pop("request_id", None)


def _panel_rpc(method: str, params: dict[str, Any] | None, timeout: float, holder: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"method": str(method), "params": dict(params or {})}).encode("utf-8")
    deadline = time.monotonic() + max(1.0, float(timeout))
    try:
        resp = _http("POST", _RPC_URL, body, timeout=min(_ROUND_TIMEOUT_S, float(timeout) + 2.0))
    except _Unreachable:
        return {"error": "panel not open"}

    while resp.get("pending"):
        request_id = str(resp.get("request_id") or "")
        holder["request_id"] = request_id
        remaining = deadline - time.monotonic()
        if not request_id or remaining <= 0:
            return {"error": "timeout", "timed_out": True}
        poll_url = f"{_RPC_URL}?id={urllib.request.quote(request_id)}"
        try:
            resp = _http("GET", poll_url, None, timeout=min(_ROUND_TIMEOUT_S, remaining + 2.0))
        except _Unreachable as exc:
            if exc.timed_out:
                # ponytail: retry one slow poll; inf wait already keeps remaining > 0
                resp = {"pending": True, "request_id": request_id}
                continue
            return {"error": "panel not open"}

    result = resp.get("result")
    return result if isinstance(result, dict) else resp


# D20: a chat whose agent runs in an agent-host process asks that host, not the app (whose
# window thread may be jammed). The host's pipe and key reach the tool server in the
# adapter's hello identity (extra host_pipe / host_key), or in this process's env for a
# dedicated bridge. Contract the host serves: one connection per call, the request
# ``{"method": ..., "params": {...}}`` and one reply, the same result dict the panel's
# /__panel_rpc gives (steer_take {lease_id, texts}, steer_ack {ok}, question_gate {blocked}).
HOST_PIPE_ENV = "DUCKY_AGENT_HOST_PIPE"
HOST_KEY_ENV = "DUCKY_AGENT_HOST_KEY"


def _host_target() -> tuple[str, bytes]:
    from backend.workspace.identity import current

    extra = getattr(current(), "extra", None) or {}
    pipe = str(extra.get("host_pipe") or os.environ.get(HOST_PIPE_ENV) or "").strip()
    key = str(extra.get("host_key") or os.environ.get(HOST_KEY_ENV) or "").strip()
    try:
        return pipe, bytes.fromhex(key)
    except ValueError:
        return pipe, b""


def host_rpc(pipe: str, authkey: bytes, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One request to the agent host over its pipe; raises OSError/EOFError/TimeoutError."""
    from multiprocessing.connection import Client

    conn = Client(pipe, authkey=authkey or None)
    try:
        conn.send_bytes(json.dumps({"method": method, "params": params}).encode("utf-8"))
        if not conn.poll(None if timeout == float("inf") else timeout):
            raise TimeoutError(f"agent host did not answer {method}")
        reply = json.loads(conn.recv_bytes())
    finally:
        conn.close()
    return reply if isinstance(reply, dict) else {}


def _agent_rpc(method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    """The steer and question checks: the chat's agent host when it has one, else the app."""
    pipe, key = _host_target()
    if pipe:
        try:
            return host_rpc(pipe, key, method, params, timeout)
        except (OSError, EOFError, ValueError):
            pass  # ponytail: host gone or unreadable → the app path, which still knows non-host chats
    return panel_rpc(method, params, timeout=timeout)


def take_steer_messages(conv_id: str) -> tuple[str, list[str]]:
    """Lease the messages the person sent into this chat's running turn: ``(lease_id, texts)``
    (frontend/ui_web/steer.py). They stay the panel's until :func:`ack_steer`; an unconfirmed
    lease returns to the queue by itself, so a slow or failed answer never loses one."""
    response = _agent_rpc("steer_take", {"conv_id": conv_id}, 10.0)
    texts = response.get("texts")
    texts = [str(t) for t in texts if str(t).strip()] if isinstance(texts, list) else []
    return str(response.get("lease_id") or ""), texts


def ack_steer(conv_id: str, lease_id: str) -> None:
    """The leased messages are in a tool result the agent gets: the panel drops them."""
    if lease_id:
        _agent_rpc("steer_ack", {"conv_id": conv_id, "lease_id": lease_id}, 5.0)


# The stop event of the tool call this context runs (bind_gate_stop). Threads started with
# asyncio.to_thread copy the context, so the gate sees it without a new argument.
_GATE_STOP: contextvars.ContextVar[Any] = contextvars.ContextVar("question_gate_stop", default=None)


def bind_gate_stop(stop: Any) -> contextvars.Token:
    """Make ``stop`` (a threading.Event) end this context's question gate once it is set."""
    return _GATE_STOP.set(stop)


def unbind_gate_stop(token: contextvars.Token) -> None:
    _GATE_STOP.reset(token)


def wait_for_question_answers(conv_id: str, stop: Any = None) -> bool:
    """Check the owning panel, including when this bridge is a separate process.

    True once no question is open. False when ``stop`` (or the bound :func:`bind_gate_stop`
    event) is set: the call was given up on, so its thread stops polling within one round
    instead of waiting for an answer that may never come.
    """
    stop = stop if stop is not None else _GATE_STOP.get()
    while True:
        if stop is not None and stop.is_set():
            return False
        response = _agent_rpc("question_gate", {"conv_id": conv_id}, float("inf"))
        if "error" in response or "blocked" not in response:
            raise RuntimeError("Cannot verify the user's answers: panel unavailable. Task tools remain blocked.")
        if response["blocked"] is False:
            return True
