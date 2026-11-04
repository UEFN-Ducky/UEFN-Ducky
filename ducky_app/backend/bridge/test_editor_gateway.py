"""Every editor entry point reaches the editor gateway: listener HTTP, Epic MCP, editor process."""

from __future__ import annotations

import asyncio

import pytest

import backend.bridge.client as bridge
from backend.bridge import editor_gateway


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, bool]]:
    calls: list[tuple[str, str, bool]] = []
    real_call, real_acall = editor_gateway.call, editor_gateway.acall

    def _call(chat_id, op, mutating, fn):
        calls.append((chat_id, op, mutating))
        return real_call(chat_id, op, mutating, fn)

    async def _acall(chat_id, op, mutating, fn):
        calls.append((chat_id, op, mutating))
        return await real_acall(chat_id, op, mutating, fn)

    monkeypatch.setattr(editor_gateway, "call", _call)
    monkeypatch.setattr(editor_gateway, "acall", _acall)
    monkeypatch.setenv("DUCKY_CONV_ID", "chat-1")
    return calls


def test_call_runs_fn_and_returns_its_result() -> None:
    assert editor_gateway.call("c", "ping", False, lambda: 7) == 7


def test_acall_awaits_a_coroutine() -> None:
    async def _fn():
        return "done"

    assert asyncio.run(editor_gateway.acall("c", "x", True, _fn)) == "done"


def test_current_chat_id_reads_the_run_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKY_CONV_ID", "chat-42")
    assert editor_gateway.current_chat_id() == "chat-42"


def test_send_command_goes_through_gateway(spy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_send_command", lambda c, p=None, t=0: {"cmd": c})
    assert bridge.send_command("ping") == {"cmd": "ping"}
    assert bridge.send_command("spawn_actor", {"x": 1}) == {"cmd": "spawn_actor"}
    assert spy == [("chat-1", "ping", False), ("chat-1", "spawn_actor", True)]


def test_post_command_to_listener_goes_through_gateway(spy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_post_command_to_listener", lambda port, c, p=None, timeout=0: {"port": port})
    assert bridge.post_command_to_listener(4200, "get_log") == {"port": 4200}
    assert bridge.post_command_to_listener(4200, "save_current_level") == {"port": 4200}
    assert spy == [("chat-1", "get_log", False), ("chat-1", "save_current_level", True)]


def test_epic_call_goes_through_gateway(spy) -> None:
    from backend.mcp_plugins import epic

    async def _send():
        return "sent"

    async def _both():
        a = await epic.call_epic_tool("call_tool", {"tool_name": "SetDeviceProperty"}, _send)
        b = await epic.call_epic_tool("list_toolsets", {}, _send)
        return a, b

    assert asyncio.run(_both()) == ("sent", "sent")
    assert spy == [("chat-1", "unreal__call_tool", True), ("chat-1", "unreal__list_toolsets", False)]


@pytest.mark.parametrize(
    ("tool", "args", "mutating"),
    [
        ("describe_toolset", {}, False),
        ("call_tool", {"tool_name": "EditorToolset.LevelToolset.GetAllActors"}, False),
        ("call_tool", {"tool_name": "find_devices"}, False),
        ("call_tool", {"tool_name": "AddToSceneFromClass"}, True),
        ("call_tool", {"tool_name": "Isolate"}, True),
        ("call_tool", {}, True),
    ],
)
def test_epic_tool_mutating(tool: str, args: dict, mutating: bool) -> None:
    from backend.mcp_plugins.epic import epic_tool_mutating

    assert epic_tool_mutating(tool, args) is mutating


def test_editor_process_tools_go_through_gateway(spy, monkeypatch: pytest.MonkeyPatch) -> None:
    import frontend.window_view as window_view
    from backend.tools.panel import ducky_panel

    monkeypatch.setattr(ducky_panel, "_island_arg", lambda project: None)
    monkeypatch.setattr(window_view, "launch_uefn_project", lambda root: {"launched": True})
    monkeypatch.setattr(window_view, "close_uefn", lambda: {"closed": True})
    monkeypatch.setattr(window_view, "restart_uefn_project", lambda root, wait, timeout: {"restarted": True})

    assert "launched" in ducky_panel.ducky_launch_uefn()
    assert "closed" in ducky_panel.ducky_close_uefn()
    assert "restarted" in ducky_panel.ducky_restart_uefn(timeout=1)
    assert spy == [
        ("chat-1", "ducky_launch_uefn", True),
        ("chat-1", "ducky_close_uefn", True),
        ("chat-1", "ducky_restart_uefn", True),
    ]
