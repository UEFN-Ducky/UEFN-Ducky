"""A coding-agent turn starts without asking the UEFN listener anything."""

from __future__ import annotations

import ast
import inspect

from backend.agent.coding_agents import runner

_LISTENER_CALLS = {"fetch_listener_status", "listener_get_health", "is_listener_ready", "probe_epic_mcp"}


def _calls(fn) -> set[str]:
    tree = ast.parse(inspect.getsource(fn))
    return {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}


def test_coding_agent_turn_makes_zero_listener_requests() -> None:
    called = _calls(runner._run_coding_agent_message)
    assert not called & _LISTENER_CALLS, called & _LISTENER_CALLS
    assert "bootstrap_system_prompt" in called  # the prompt is still built, without listener state


def test_coding_agent_bootstrap_gets_no_listener_state() -> None:
    tree = ast.parse(inspect.getsource(runner._run_coding_agent_message))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "bootstrap_system_prompt":
            assert "listener_online" not in {kw.arg for kw in node.keywords}
            break
    else:
        raise AssertionError("bootstrap_system_prompt call not found")
