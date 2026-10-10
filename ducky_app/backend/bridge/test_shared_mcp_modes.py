"""External callers share the embedded agent's execution policy."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.bridge import shared_mcp
from backend.agent.run_context import current_mode
from backend.agent.toolsets import plan_safe


class ModeMcp:
    _ducky_skip_plugin_wait = True

    def __init__(self, names, sync):
        self.calls = []
        self._tool_manager = SimpleNamespace(_tools={
            n: SimpleNamespace(name=n, is_async=not sync, inputSchema={}) for n in names
        })

    async def list_tools(self):
        return list(self._tool_manager._tools.values())

    async def call_tool(self, name, arguments):
        self.calls.append((name, current_mode()))
        return [{"type": "text", "text": "executed"}]


@pytest.mark.parametrize("agent", ["codex", "claude_code", "cursor"])
@pytest.mark.parametrize("mode", ["ask", "plan", "agent"])
@pytest.mark.parametrize("sync", [False, True])
def test_external_mode_policy(monkeypatch, agent, mode, sync):
    writes = set(plan_safe._BLOCK_EXACT) | {
        prefix + "thing" for prefix in plan_safe._BLOCK_PREFIXES
    } | {"workspace_write_file", "ducky_terminal_run", "spawn_actor", "workspace_git"}
    reads = set(plan_safe.ASK_READ_TOOLS)
    plans = set(plan_safe.PLAN_BOOKKEEPING_TOOLS)
    names = writes | reads | plans | {"ducky_call_tool"}
    mcp = ModeMcp(names, sync)
    conv = SimpleNamespace(messages=[{
        "role": "assistant", "run_id": "run", "requested_mode": mode,
    }])
    monkeypatch.setattr("frontend.ui_web.project_chats.load_conversation", lambda cid: conv)
    ident = shared_mcp.identity_from_payload({"conv_id": "chat", "run_id": "run", "coding_agent": agent, "mode": "agent"})

    async def check():
        for name in sorted(names - {"ducky_call_tool"}):
            result = await shared_mcp._call_tool(mcp, name, {}, ident)
            allowed = mode == "agent" or name in reads or (mode == "plan" and name in plans)
            assert bool(result.get("isError")) is not allowed, name
            if allowed:
                assert mcp.calls[-1] == (name, mode)
            else:
                assert mode.title() + " mode" in result["content"][0]["text"]
                assert name not in [call[0] for call in mcp.calls]
            assert current_mode() == "agent"
        result = await shared_mcp._call_tool(mcp, "ducky_call_tool", {
            "name": "workspace_write_file", "arguments": {}
        }, ident)
        assert bool(result.get("isError")) is (mode != "agent")
    asyncio.run(check())


def test_mode_is_reloaded_for_every_call(monkeypatch):
    conv = SimpleNamespace(messages=[{"role": "assistant", "run_id": "run", "requested_mode": "agent"}])
    monkeypatch.setattr("frontend.ui_web.project_chats.load_conversation", lambda cid: conv)
    ident = shared_mcp.identity_from_payload({"conv_id": "chat", "run_id": "run", "coding_agent": "codex"})
    mcp = ModeMcp(["workspace_write_file"], False)
    async def check():
        assert not (await shared_mcp._call_tool(mcp, "workspace_write_file", {}, ident)).get("isError")
        conv.messages[0]["requested_mode"] = "ask"
        assert (await shared_mcp._call_tool(mcp, "workspace_write_file", {}, ident))["isError"]
        assert mcp.calls == [("workspace_write_file", "agent")]
    asyncio.run(check())


@pytest.mark.parametrize("mode", [None, "invalid"])
def test_unknown_external_mode_does_not_execute(monkeypatch, mode):
    messages = [] if mode is None else [{"role": "assistant", "run_id": "run", "requested_mode": mode}]
    monkeypatch.setattr("frontend.ui_web.project_chats.load_conversation", lambda cid: SimpleNamespace(messages=messages))
    ident = shared_mcp.identity_from_payload({"conv_id": "chat", "run_id": "run", "coding_agent": "codex"})
    mcp = ModeMcp(["workspace_write_file"], False)
    with pytest.raises(ValueError, match="mode"):
        asyncio.run(shared_mcp._call_tool(mcp, "workspace_write_file", {}, ident))
    assert mcp.calls == []
