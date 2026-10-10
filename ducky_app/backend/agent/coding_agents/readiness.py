"""Keep outside agents behind the app-owned shared tool server's readiness gate."""
from __future__ import annotations

import time

from backend.agent.coding_agents.base import CodingAgentLaunchResult


def launch_with_ready_tools(adapter, **kwargs):
    """Start the daemon in the window process and wait before sending a prompt."""
    from backend.bridge import shared_mcp

    cancel = kwargs.get("cancel")
    session = kwargs.get("session_id", "")
    deadline = time.monotonic() + 20.0
    def cancelled():
        return cancel is not None and cancel.is_set()
    def failed(status="error"):
        return CodingAgentLaunchResult(ok=False, status=status, upstream_session_id=session,
            error="Cancelled" if status == "cancelled" else "Ducky's tools didn't start")
    if cancelled():
        return failed("cancelled")
    if not shared_mcp.enabled():
        # Shared tools off (the default): the agent's MCP config starts its own Ducky
        # tool process, so there is no shared server to wait for.
        return adapter.launch(**kwargs)
    try:
        started = shared_mcp.start_daemon_from_app()
        if not started.get("ok"):
            return failed()
        while not cancelled():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return failed()
            if shared_mcp._daemon_answers(timeout_s=min(3.0, remaining)):
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                if cancel is not None:
                    cancel.wait(min(0.25, remaining))
                else:
                    time.sleep(min(0.25, remaining))
    except (OSError, RuntimeError, ValueError):
        return failed()
    if cancelled():
        return failed("cancelled")
    return adapter.launch(**kwargs)
