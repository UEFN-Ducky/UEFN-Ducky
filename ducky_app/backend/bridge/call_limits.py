"""How long a shared-MCP tool call may run before it is stopped (see shared_mcp._watched).

Tests patch these names on this module: ``call_limit`` reads them at call time.
"""

from __future__ import annotations

import threading

# Answered from Ducky's own tool catalog and skill index: a call past this never answers,
# so it is stopped and the agent hears why. Oct 18 2026: a user's agent sat 29 minutes on
# ducky_find_tools; agents (Claude Code) wait on an MCP call with no limit of their own.
QUICK_TOOLS = frozenset({"ducky_get_tools", "ducky_find_tools", "skill_search", "skill_read_subskill"})
QUICK_TOOL_LIMIT_S = 60.0
# Reads that answer in milliseconds. Nov 4 2026: 13 of these hung until Claude Code's
# 1800 s abort; past this limit the agent hears why instead of waiting half an hour.
QUICK_READS = frozenset({
    "ducky_get_plan", "ducky_health", "ducky_get_status", "ducky_agent_list", "ducky_agent_transcript",
    "ducky_agent_inbox", "ducky_list_chats", "ducky_read_chat", "ducky_terminal_read_output",
    "ducky_terminal_list", "ducky_get_errors", "workspace_read_file", "workspace_read_files",
    "workspace_search", "workspace_find", "workspace_file_outline", "workspace_list_dir", "workspace_tree",
    "workspace_git", "project_memory_list", "project_memory_get",
})
QUICK_READ_LIMIT_S = 30.0
# A Store plugin or nested MCP tool run through ducky_call_tool: Codex waits on an MCP call
# forever, and 9 of 15 stuck calls on Oct 31 2026 were these. Long enough for a render or
# a 3D generation; a tool that waits by design (WAITS_BY_DESIGN) has no limit.
# ponytail: one limit for every plugin tool; a per-tool limit if a real tool needs longer.
PLUGIN_CALL_LIMIT_S = 900.0
# Epic's UEFN MCP called directly (unreal__*): Nov 6 2026 had 44 unreal__call_tool calls stuck
# with no limit, mostly behind a UEFN popup nobody answered.
UNREAL_CALL_LIMIT_S = 180.0
UNREAL_LIST_LIMIT_S = 60.0
UNREAL_LISTS = frozenset({"unreal__list_toolsets", "unreal__describe_toolset"})
POPUP_READ_S = 1.0
# Calls that wait by design (on the person, or a command / workflow / window with its own
# timeout): still recorded where they wait, but not counted as an anonymous agent.stuck,
# which said "stuck" 117 times a day for questions and test suites (Oct 25 2026).
WAITS_BY_DESIGN = frozenset({
    "ducky_ask_user", "ducky_terminal_run", "sandbox_run", "run_workflow", "run_workflow_node",
    "ducky_wait_uefn_ready", "uefn_wait_window", "uefn_logs_wait", "fortnite_client_wait", "playtester_wait",
})


def call_limit(name: str, inner: str) -> float | None:
    """Seconds a call may take before it is stopped; None = no limit (it may wait by design)."""
    if inner in QUICK_READS:
        return QUICK_READ_LIMIT_S
    if inner in QUICK_TOOLS:
        return QUICK_TOOL_LIMIT_S
    if inner == "unreal__call_tool":
        return UNREAL_CALL_LIMIT_S
    if inner in UNREAL_LISTS:
        return UNREAL_LIST_LIMIT_S
    if name == "ducky_call_tool" and inner != name and inner not in WAITS_BY_DESIGN:
        return PLUGIN_CALL_LIMIT_S
    return None


def _popup_titles() -> list[str]:
    from backend.tools.core.uefn_popups import list_popups

    return [str(p.get("title") or "").strip() for p in list_popups() if str(p.get("title") or "").strip()]


def open_popups(wait_s: float | None = None) -> list[str]:
    """Up to 3 open UEFN popup titles, read within ``wait_s`` (default POPUP_READ_S); [] on any failure."""
    found: list[str] = []

    def _read() -> None:
        try:
            found.extend(_popup_titles()[:3])
        except Exception:  # noqa: BLE001 — the message works without them
            pass

    reader = threading.Thread(target=_read, daemon=True, name="call-limit-popups")
    reader.start()
    reader.join(POPUP_READ_S if wait_s is None else wait_s)
    return list(found) if not reader.is_alive() else []


def timed_out_message(inner: str, limit: float) -> str:
    """What the agent hears when a call is stopped at its limit. Blocks up to POPUP_READ_S."""
    if inner.startswith("unreal__"):
        titles = open_popups()
        popup = f" Open popup: {', '.join(titles)}. Answer it with uefn_popups / uefn_popup_press." if titles else ""
        return (f"UEFN did not answer in {limit:.0f} s ({inner} was stopped).{popup} If UEFN is busy "
                "(compiling, loading, saving), call it once more; if it stops again, carry on without "
                "it and tell the person.")
    return (f"{inner} did not answer within {limit:.0f} s and was stopped. If this chat asked "
            "the person a question, tools wait for that answer first. Otherwise call it once "
            "more; if it stops again, carry on without it and tell the person.")
