"""Tester suite — device graph sim, Verse harness, session probes."""

from __future__ import annotations

from typing import Any

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "device_graph_snapshot",
        "device_graph_audit",
        "tester_list_devices",
        "tester_list_tests",
        "tester_create_simulation",
        "tester_run_simulation",
        "verse_test_add_case",
        "verse_test_results",
        "tester_get_results",
        "session_status",
        "simulate_device_event",
        "verse_test_scaffold",
        "verse_test_run",
        "actor_state_snapshot",
        "actor_state_diff",
        "get_editor_log",
        "workspace_compile_verse",
        "workspace_push_verse_changes",
        "workspace_list_verse_errors",
        "workspace_read_file",
        "workspace_write_file",
        "inspect_verse_device",
        "take_high_res_screenshot",
    }
)

# Full MCP surface for the Tester Ducky — use via ducky_call_tool (not tools[]).
TESTER_PROFILE_TOOLS = CORE_TOOLS | EXTENDED_TOOLS

TOOLS = TESTER_PROFILE_TOOLS

PLAN_TOOLS = frozenset(
    {
        "device_graph_snapshot",
        "device_graph_audit",
        "tester_list_devices",
        "tester_list_tests",
        "session_status",
    }
)


def is_tester_conv(conv: Any) -> bool:
    """True when user-written details name testing tools (not profile-name hardcoding).

    Mentions any ``TESTER_PROFILE_TOOLS`` name, or the ``verse_test_`` / ``tester_``
    prefixes in personality/when_to_use. Bundled Tester qualifies via its template
    text; a renamed/custom QA ducky qualifies the same way.
    """
    from backend.agent.profile_details import profile_detail_text, tools_named_in_profile

    if tools_named_in_profile(conv, TESTER_PROFILE_TOOLS):
        return True
    text = profile_detail_text(conv)
    return "verse_test_" in text or "tester_" in text
