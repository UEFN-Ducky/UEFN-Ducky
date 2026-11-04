"""Every editor entry point reaches the editor gateway: listener HTTP, Epic MCP, editor process."""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.bridge.client as bridge
from backend.bridge import editor_gateway


@pytest.fixture(autouse=True)
def _own_lease(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Each test gets its own lease file and a stub title lookup."""
    monkeypatch.setattr(editor_gateway, "_lease_path", lambda: tmp_path / "editor_lease.json")
    monkeypatch.setattr(editor_gateway, "_chat_title", lambda chat_id: {"lead": "Lead: Limo Ride"}.get(chat_id, chat_id))


@pytest.fixture(autouse=True)
def crash_watch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Lines the gateway appends to the project's crash-watch memory."""
    lines: list[str] = []
    monkeypatch.setattr(editor_gateway, "_log_crash_watch", lines.append)
    return lines


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

    monkeypatch.setattr(editor_gateway, "_ask_person", lambda chat_id, verb, reason: True)
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


def test_agent_restart_refused_without_the_persons_yes(clock, crash_watch, monkeypatch) -> None:
    asked: list[tuple[str, str, str]] = []
    monkeypatch.setattr(editor_gateway, "_ask_person", lambda *a: asked.append(a) or False)
    ran: list[str] = []
    with pytest.raises(editor_gateway.EditorProcessRefused, match="did not allow"):
        editor_gateway.call_editor_process(
            "c4c94232-aaaa", "ducky_restart_uefn", "UEFN closed during build", lambda: ran.append("x")
        )
    assert ran == []
    assert asked == [("c4c94232-aaaa", "restart", "UEFN closed during build")]
    assert crash_watch[0].endswith("restart asked by c4c94232: 'UEFN closed during build' -> person said no (or did not answer)")


def test_agent_restart_runs_after_yes_and_is_logged(clock, crash_watch, monkeypatch) -> None:
    monkeypatch.setattr(editor_gateway, "_ask_person", lambda *a: True)
    out = editor_gateway.call_editor_process(
        "c4c94232-aaaa", "ducky_restart_uefn", "UEFN closed during build", lambda: "restarted"
    )
    assert out == "restarted"
    stamp, rest = crash_watch[0].split(" ", 1)
    assert len(stamp) == 5 and stamp[2] == ":"
    assert rest == "restart asked by c4c94232: 'UEFN closed during build' -> person said yes"


def test_the_persons_own_restart_is_not_asked_but_logged(crash_watch, monkeypatch) -> None:
    monkeypatch.setattr(editor_gateway, "_ask_person", lambda *a: pytest.fail("must not ask the person"))
    assert editor_gateway.call_editor_process("", "ducky_close_uefn", "", lambda: "closed") == "closed"
    assert crash_watch[0].endswith("close asked by the person -> by the person")


def test_ask_person_reads_the_card_answer(monkeypatch) -> None:
    from backend.tools.panel import panel_ui

    replies = iter([
        {"ok": True, "answers": {"editor_process": {"selected": ["yes"]}}},
        {"ok": True, "answers": {"editor_process": {"selected": ["no"]}}},
        {"error": "timed out waiting for the person"},
    ])
    monkeypatch.setattr(panel_ui, "ducky_ask_user", lambda questions, title="": json.dumps(next(replies)))
    assert editor_gateway._ask_person("lead", "restart", "why") is True
    assert editor_gateway._ask_person("lead", "restart", "why") is False
    assert editor_gateway._ask_person("lead", "restart", "why") is False


def test_importing_the_client_starts_no_heartbeat_thread() -> None:
    import os
    import subprocess
    import sys

    code = (
        "import threading\n"
        "before = set(threading.enumerate())\n"
        "import backend.bridge.client as c\n"
        "new = [t.name for t in threading.enumerate() if t not in before]\n"
        "print(repr(new), hasattr(c, '_heartbeat_loop'))\n"
    )
    app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=app_dir, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[] False"


@pytest.fixture
def uefn_closed(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every listener connection is refused; returns the requests that were tried."""
    import urllib.error
    import urllib.request

    tried: list[object] = []

    def _urlopen(req, *args, **kwargs):
        tried.append(req)
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    bridge._invalidate_cache()  # an earlier test's cached ping would answer without a request
    monkeypatch.setattr(bridge, "_pinned_port", None)
    monkeypatch.setattr(bridge, "_discovered_port", None)
    return tried


def test_send_command_with_uefn_closed_is_one_attempt(uefn_closed) -> None:
    with pytest.raises(ConnectionError, match="UEFN isn't open with an island"):
        bridge.send_command("spawn_actor", {"x": 1})
    assert len(uefn_closed) == 1
    assert uefn_closed[0].get_method() == "POST"


def test_post_command_with_uefn_closed_is_one_attempt(uefn_closed) -> None:
    with pytest.raises(ConnectionError, match="UEFN isn't open with an island"):
        bridge.post_command_to_listener(4200, "get_log")
    assert len(uefn_closed) == 1


def test_every_listener_request_names_its_client(uefn_closed, monkeypatch) -> None:
    monkeypatch.setenv("UEFN_DUCKY_PROCESS_ROLE", "app")
    monkeypatch.delenv("DUCKY_RUN_ID", raising=False)
    monkeypatch.delenv("DUCKY_CONV_ID", raising=False)
    with pytest.raises(ConnectionError):
        bridge.send_command("ping")
    bridge.listener_get_health(4200)
    bridge.listener_ping_port(4200)
    import os

    tags = [req.get_header("X-ducky-client") for req in uefn_closed]
    assert tags == [f"panel:{os.getpid()}"] * 3


@pytest.mark.parametrize(
    ("role", "argv", "run_id", "tag"),
    [
        ("app", ["ducky"], "", "panel"),
        ("tool_server", ["x", "bridge", "--shared-daemon"], "", "shared-daemon"),
        ("tool_server", ["x", "bridge", "--port", "4201"], "run-7", "adapter"),
        ("agent_host", ["x"], "", "agent-host"),
    ],
)
def test_client_tag_roles(role, argv, run_id, tag, monkeypatch) -> None:
    import os
    import sys

    monkeypatch.setenv("UEFN_DUCKY_PROCESS_ROLE", role)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("DUCKY_CONV_ID", raising=False)
    if run_id:
        monkeypatch.setenv("DUCKY_RUN_ID", run_id)
    else:
        monkeypatch.delenv("DUCKY_RUN_ID", raising=False)
    expected = f"{tag}:{os.getpid()}" + (f":{run_id}" if run_id else "")
    assert bridge.client_tag() == expected


def test_panel_tool_reports_a_refused_restart(monkeypatch) -> None:
    from backend.tools.panel import ducky_panel

    monkeypatch.setenv("DUCKY_CONV_ID", "chat-9")
    monkeypatch.setattr(editor_gateway, "_ask_person", lambda *a: False)
    monkeypatch.setattr(ducky_panel, "_island_arg", lambda project: None)
    out = json.loads(ducky_panel.ducky_restart_uefn(timeout=1, reason="test"))
    assert out["ok"] is False and out["cancelled"] is True
    assert "did not allow" in out["error"]
