"""Every editor entry point reaches the editor gateway: listener HTTP, Epic MCP, editor process."""

from __future__ import annotations

import asyncio

import pytest

import backend.bridge.client as bridge
from backend.bridge import editor_gateway


@pytest.fixture(autouse=True)
def _own_lease(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Each test gets its own lease file and a stub title lookup."""
    monkeypatch.setattr(editor_gateway, "_lease_path", lambda: tmp_path / "editor_lease.json")
    monkeypatch.setattr(editor_gateway, "_chat_title", lambda chat_id: {"lead": "Lead: Limo Ride"}.get(chat_id, chat_id))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(editor_gateway, "_now", lambda: now[0])
    return now


def test_other_chats_mutating_call_is_busy(clock) -> None:
    assert editor_gateway.call("lead", "spawn_actor", True, lambda: "ok") == "ok"
    clock[0] += 12
    ran: list[str] = []
    with pytest.raises(editor_gateway.EditorBusy, match=r"editor busy: held by Lead: Limo Ride \(12 s\)"):
        editor_gateway.call("trio", "spawn_actor", True, lambda: ran.append("x"))
    assert ran == []


def test_reads_and_chatless_calls_always_pass(clock) -> None:
    editor_gateway.call("lead", "spawn_actor", True, lambda: None)
    assert editor_gateway.call("trio", "get_all_actors", False, lambda: "read") == "read"
    assert editor_gateway.call("", "save_current_level", True, lambda: "person") == "person"


def test_lease_renews_on_each_call_and_lapses_after_idle(clock) -> None:
    editor_gateway.call("lead", "a", True, lambda: None)
    clock[0] += 50
    editor_gateway.call("lead", "b", True, lambda: None)  # renews
    clock[0] += 50  # 100 s after taking it, 50 s idle
    with pytest.raises(editor_gateway.EditorBusy, match=r"\(100 s\)"):
        editor_gateway.call("trio", "c", True, lambda: None)
    clock[0] += editor_gateway.LEASE_IDLE_SEC
    assert editor_gateway.call("trio", "c", True, lambda: "mine") == "mine"
    assert editor_gateway.lease_holder()["chat_id"] == "trio"


def test_released_lease_lets_the_next_chat_in(clock) -> None:
    editor_gateway.call("lead", "a", True, lambda: None)
    editor_gateway.release_lease("lead")
    assert editor_gateway.call("trio", "a", True, lambda: "ok") == "ok"


def test_acall_is_refused_too(clock) -> None:
    editor_gateway.call("lead", "a", True, lambda: None)

    async def _fn():
        return "sent"

    with pytest.raises(editor_gateway.EditorBusy):
        asyncio.run(editor_gateway.acall("trio", "unreal__call_tool", True, _fn))


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
