"""How long a shared-MCP tool call may run before it is stopped (see shared_mcp._watched).

Tests patch these names on this module: ``call_limit`` reads them at call time.
"""

from __future__ import annotations

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
# Epic's UEFN MCP (unreal__*) has no limit here: a UEFN call waits while UEFN works and ends
# when it answers, its connection drops or the person presses Stop (client_pool).
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
    if inner.startswith("unreal__"):
        return None
    if name == "ducky_call_tool" and inner != name and inner not in WAITS_BY_DESIGN:
        return PLUGIN_CALL_LIMIT_S
    return None


def timed_out_message(inner: str, limit: float) -> str:
    """What the agent hears when a call is stopped at its limit."""
    return (f"{inner} did not answer within {limit:.0f} s and was stopped. If this chat asked "
            "the person a question, tools wait for that answer first. Otherwise call it once "
            "more; if it stops again, carry on without it and tell the person.")
