"""Listener revive: reload when up, turn Python on in UEFN when down, auto-try once."""

from __future__ import annotations

import pytest

from backend.bridge import revive


@pytest.fixture
def epic(monkeypatch):
    calls: list[str] = []
    state = {"online": True, "python": "false"}

    def call(tool_name: str) -> str:
        calls.append(tool_name)
        return '{"result":"{\\"returnValue\\":%s}"}' % state["python"]

    monkeypatch.setattr(revive, "_call_epic_tool", call)
    monkeypatch.setattr(revive, "_refresh_boot_hooks", lambda: None)
    monkeypatch.setattr(revive, "_port_held_by_uefn", lambda port: state.get("held", False))
    monkeypatch.setattr("backend.mcp_plugins.epic.probe_epic_mcp", lambda **_: {"epic_mcp_online": state["online"]})
    monkeypatch.setattr("backend.mcp_plugins.epic.invalidate_epic_mcp_probe", lambda: None)
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda port: None)
    return calls, state


def test_listener_up_reloads(monkeypatch, epic) -> None:
    calls, _ = epic
    posted: list[str] = []
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda port: {"status": "ok"})
    monkeypatch.setattr("backend.bridge.post_command_to_listener", lambda port, cmd, **_: posted.append(cmd) or {})
    out = revive.revive_listener(4200)
    assert out["action"] == "reloaded" and out["ok"]
    assert posted == ["reload_listener"] and calls == []


def test_python_off_gets_enabled(epic) -> None:
    calls, _ = epic
    out = revive.revive_listener(4200)
    assert out["action"] == "python_enabled" and out["ok"]
    assert calls == ["IsPythonEnabledInUEFN", "EnablePythonInUEFN"]


def test_python_on_does_not_enable_again(epic) -> None:
    calls, state = epic
    state["python"] = "true"
    out = revive.revive_listener(4200)
    assert out["action"] == "python_already_on" and not out["ok"]
    assert calls == ["IsPythonEnabledInUEFN"]


def test_python_on_with_dead_socket_says_restart(epic) -> None:
    calls, state = epic
    state["python"] = "true"
    state["held"] = True
    out = revive.revive_listener(4200)
    assert out["action"] == "stale_socket" and not out["ok"]
    assert calls == ["IsPythonEnabledInUEFN"]


def test_uefn_closed(epic) -> None:
    calls, state = epic
    state["online"] = False
    out = revive.revive_listener(4200)
    assert out["action"] == "uefn_offline" and calls == []


def test_auto_revive_once_per_offline_spell(monkeypatch) -> None:
    runs: list[int] = []
    monkeypatch.setattr(revive.AutoRevive, "_run", staticmethod(lambda port: runs.append(port)))

    class _Thread:
        def __init__(self, target, args, **_):
            self._go = lambda: target(*args)

        def start(self):
            self._go()

    monkeypatch.setattr(revive.threading, "Thread", _Thread)
    auto = revive.AutoRevive(grace_sec=60)
    down = {"online": False, "epic_mcp_online": True}
    assert not auto.observe(down, 4200, now=100)
    assert not auto.observe(down, 4200, now=150)
    assert auto.observe(down, 4200, now=161)
    assert not auto.observe(down, 4200, now=500)
    assert runs == [4200]
    # UEFN closed / next island: a fresh spell gets its own try.
    auto.observe({"online": False, "epic_mcp_online": False}, 4200, now=600)
    auto.observe(down, 4200, now=700)
    assert auto.observe(down, 4200, now=761)
    assert runs == [4200, 4200]


def test_dead_epic_session_is_dropped_and_tried_once_more(monkeypatch) -> None:
    from backend.mcp_plugins import client_pool

    tries: list[str] = []
    forgot: list[str] = []

    class _Pool:
        def call_tool(self, name, args):
            tries.append(args["tool_name"])
            if len(tries) == 1:
                raise RuntimeError("Nested MCP 'unreal-mcp' session unavailable for tool 'call_tool': boom")
            return "ok"

        def run_sync(self, value, timeout):
            return value

        def forget(self, plugin_id):
            forgot.append(plugin_id)

    monkeypatch.setattr(client_pool, "get_plugin_pool", lambda: _Pool())
    monkeypatch.setattr(revive, "_SESSION_RETRY_SEC", 0)
    assert revive._call_epic_tool("EnablePythonInUEFN") == "ok"
    assert tries == ["EnablePythonInUEFN", "EnablePythonInUEFN"] and len(forgot) == 1


def test_auto_revive_error_code_names_the_step(monkeypatch) -> None:
    from frontend.usage_events import where_of

    logged: list[str] = []
    monkeypatch.setattr("frontend.error_log.record_error", lambda src, _msg: logged.append(src))
    for action in ("uefn_offline", "stale_socket", "python_already_on"):
        monkeypatch.setattr(revive, "revive_listener", lambda port, a=action: {"ok": False, "action": a, "message": "x"})
        revive.AutoRevive._run(4200)
    monkeypatch.setattr(revive, "revive_listener", lambda port: 1 / 0)
    revive.AutoRevive._run(4200)
    # UEFN closing mid-try is status, not an error.
    assert logged == ["listener_revive.stale_socket", "listener_revive.python_already_on", "listener_revive.failed"]
    assert where_of(logged[0]) == "bridge"


def test_auto_revive_idle_while_listener_online() -> None:
    auto = revive.AutoRevive(grace_sec=0)
    assert not auto.observe({"online": True, "epic_mcp_online": True}, 4200, now=1)
