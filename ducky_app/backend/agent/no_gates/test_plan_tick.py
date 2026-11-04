"""An unticked plan step never blocks a tool; the result may carry a one-line reminder."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.agent.tools as tools
from backend.agent.coding_agents import plans
from backend.uefn_plugins.plugin_host.testing import patch_host

pytestmark = pytest.mark.usefixtures("unrestricted_tools")

_PENDING_PLAN = {
    "id": "p1",
    "chat_id": "chat-a",
    "title": "Work",
    "nodes": [
        {"id": "write", "content": "Write the file", "status": "pending"},
        {"id": "test", "content": "Run the tests", "status": "pending"},
    ],
}


def _fake_mcp(monkeypatch, ran: list[str]) -> None:
    async def _fake_call(name: str, args: dict):
        ran.append(name)
        return [SimpleNamespace(text='{"ok": true}')]

    mcp = MagicMock()
    mcp.call_tool = _fake_call
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)


def test_write_and_terminal_run_with_no_in_progress_step(monkeypatch) -> None:
    monkeypatch.setattr(plans, "load_active_plan", lambda: _PENDING_PLAN)
    patch_host(monkeypatch, "is_plugin_host_only_tool", lambda _n: False)
    ran: list[str] = []
    _fake_mcp(monkeypatch, ran)

    for name in ("workspace_write_file", "ducky_terminal_run"):
        result = asyncio.run(tools.execute_tool(name, {}))
        assert result.ok, (name, result.error)
        assert "Plan tick required" not in (result.error or "")
    assert ran == ["workspace_write_file", "ducky_terminal_run"]


@pytest.mark.parametrize("name", ["workspace_write_file", "ducky_terminal_run", "spawn_actor", "save_current_level"])
def test_block_reason_is_always_none(monkeypatch, name) -> None:
    # The coding-agent bridge gate still calls this until Wave 2: it must never refuse.
    monkeypatch.setattr(plans, "load_active_plan", lambda: _PENDING_PLAN)
    assert plans.plan_mutator_block_reason(name) is None
