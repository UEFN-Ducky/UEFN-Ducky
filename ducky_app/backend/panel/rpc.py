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

import json
import time
import urllib.error
import urllib.request
from typing import Any

from frontend.settings import PANEL_LISTENER_PORT

# Same loopback control port the event/run bridges already use (panel UI HTTP).
_RPC_URL = f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_rpc"

# Cap per-HTTP-round wait so a long require_click never blocks one connection
# for minutes; must stay below the handler's own wait so a round trip completes.
_ROUND_TIMEOUT_S = 25.0


class _Unreachable(Exception):
    """Panel control port refused the connection (no panel open)."""


def _http(method: str, url: str, body: bytes | None, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        # Connection refused / DNS / socket → no reachable panel.
        raise _Unreachable(str(exc)) from exc
    except (TimeoutError, OSError) as exc:
        raise _Unreachable(str(exc)) from exc
    return json.loads(raw) if raw else {}


def panel_rpc(method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
    """Invoke a panel UI method and return its result dict.

    ``timeout`` is the overall budget in seconds. On success returns the panel's
    result payload; on failure returns a dict with an ``error`` key
    (``"panel not open"`` or ``"timeout"``) — callers should surface it rather
    than raise.
    """
    body = json.dumps({"method": str(method), "params": dict(params or {})}).encode("utf-8")
    deadline = time.monotonic() + max(1.0, float(timeout))
    try:
        resp = _http("POST", _RPC_URL, body, timeout=min(_ROUND_TIMEOUT_S, float(timeout) + 2.0))
    except _Unreachable:
        return {"error": "panel not open"}

    while resp.get("pending"):
        request_id = str(resp.get("request_id") or "")
        remaining = deadline - time.monotonic()
        if not request_id or remaining <= 0:
            return {"error": "timeout", "timed_out": True}
        poll_url = f"{_RPC_URL}?id={urllib.request.quote(request_id)}"
        try:
            resp = _http("GET", poll_url, None, timeout=min(_ROUND_TIMEOUT_S, remaining + 2.0))
        except _Unreachable:
            return {"error": "panel not open"}

    result = resp.get("result")
    return result if isinstance(result, dict) else resp
