"""ducky_health / ducky_self_care: Ducky checks itself when it feels slow, says why, fixes it."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json


def _in_window(method: str, args: dict[str, Any], timeout_s: float = 90.0) -> dict[str, Any]:
    from frontend.ui_web.agent_turns import push as agent_push

    if not agent_push._in_bridge_process():
        from frontend import self_health

        return self_health.snapshot(**args) if method == "ducky_health_snapshot" else self_health.self_care(**args)
    from frontend.settings import PANEL_LISTENER_PORT

    req = urllib.request.Request(f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/{method}",
                                 data=json.dumps({"args": args}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Ducky's window didn't answer ({exc}): it may be frozen or closed. "
                                      "A frozen window comes back by itself within a minute; if not, restart Ducky."}
    result = payload.get("result", payload) if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else {"ok": False, "error": "Ducky's window sent an unreadable answer."}


@mcp.tool()
def ducky_health(detail: bool = False, pretty: bool = False) -> str:
    """Why is Ducky (or the PC) slow? One call: Ducky's window process (CPU over one second,
    memory, threads, handles), its helper processes (WebView2, shells, servers), other Ducky
    processes (leftover tool servers flagged), sandbox programs, terminals, disk use, UEFN's
    memory, the PC's memory and top apps — and ``findings``: the likely causes in plain words,
    each with its fix (a ducky_self_care action, or what the person should do). Tell the person
    what it found, in their words, before fixing. detail=true adds the latest UI-lag report."""
    return tool_json(_in_window("ducky_health_snapshot", {"detail": bool(detail)}), pretty=pretty)


@mcp.tool()
def ducky_self_care(action: str, target: str = "", pretty: bool = False) -> str:
    """Fix what ducky_health found. action: stop_sandbox (target = a chat id, or all),
    close_idle_terminals (agent terminals that are done; never the person's or running ones),
    stop_orphan_bridges (Ducky tool servers whose IDE or agent is gone), trim_caches (video
    downloads), prune_sandboxes (old ones and deleted chats'), collect_garbage, free_uefn_memory
    (gives the PC back the RAM UEFN holds but isn't using: freed_gb; safe while UEFN works, its
    undo history is kept). Returns what it did and the app's memory before and after."""
    return tool_json(_in_window("ducky_self_care_run", {"action": action, "target": target}), pretty=pretty)
