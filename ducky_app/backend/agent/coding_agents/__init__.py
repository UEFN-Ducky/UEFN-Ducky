"""Bring-your-own coding agents (Claude Code, Codex, Cursor) for UEFN-Ducky."""

from __future__ import annotations

from backend.agent.coding_agents.base import (
    CODING_AGENT_IDS,
    CodingAgentCapabilities,
    CodingAgentInfo,
    CodingAgentLaunchResult,
    detect_all,
    get_adapter,
    list_coding_agents,
    normalize_coding_agent,
)
from backend.agent.coding_agents.runner import run_coding_agent_message

# Keep host helpers in the freeze graph — Store gateway plugins import these;
# without a core reference PyInstaller can omit them and register() fails.
from backend.agent.coding_agents import cli_pty as _cli_pty  # noqa: F401
from backend.agent.coding_agents import cli_shared as _cli_shared  # noqa: F401
from backend.agent.coding_agents import proc_exec as _proc_exec  # noqa: F401

__all__ = [
    "CODING_AGENT_IDS",
    "CodingAgentCapabilities",
    "CodingAgentInfo",
    "CodingAgentLaunchResult",
    "detect_all",
    "get_adapter",
    "list_coding_agents",
    "normalize_coding_agent",
    "run_coding_agent_message",
]
