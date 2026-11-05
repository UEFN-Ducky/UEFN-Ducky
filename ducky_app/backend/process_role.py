"""What this Ducky process is: ``app`` (the window), ``tool_server`` (an MCP bridge) or
``agent_host`` (a supervisor child that runs agent turns).

Work only the window should do (pollers, presence, sync rounds, crash heal) checks
``role() == "app"``; "an agent made this" checks treat any other role as an agent.
"""

from __future__ import annotations

import os
import sys

ENV = "UEFN_DUCKY_PROCESS_ROLE"
ROLES = ("app", "tool_server", "agent_host")


def role() -> str:
    value = os.environ.get(ENV, "").strip()
    if value == "agent_host" and _shared_call():
        # An agent host serves its agents' tools in-process: a tool call there acts as the
        # tool server it replaced (asks the app for runs, never treats the host's as all).
        return "tool_server"
    if value in ROLES:
        return value
    return "tool_server" if os.environ.get("UEFN_DUCKY_MCP_BRIDGE") == "1" else "app"


def _shared_call() -> bool:
    identity = sys.modules.get("backend.workspace.identity")  # never imported from here
    try:
        return bool(identity is not None and identity.is_shared_call())
    except Exception:  # noqa: BLE001
        return False


def set_role(value: str) -> None:
    """Set before anything that checks it is imported."""
    if value not in ROLES:
        raise ValueError(f"unknown process role: {value}")
    os.environ[ENV] = value
    if value == "tool_server":
        os.environ["UEFN_DUCKY_MCP_BRIDGE"] = "1"


def is_app() -> bool:
    return role() == "app"
