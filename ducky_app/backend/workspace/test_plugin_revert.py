"""Plugin inverse dispatch: FastMCP, not the UEFN listener."""

from __future__ import annotations

from backend.workspace import plugin_revert as pr


def test_program_of_slot() -> None:
    assert pr.program_of_slot("uefn://actor/x/label") == "uefn"
    assert pr.program_of_slot("blender://object/Cube/mesh") == "blender"
    assert pr.program_of_slot("Content/Verse/a.verse") == "file"


def test_kwargs_for_keeps_only_the_callable_params() -> None:
    def fn(code: str, extra: int = 1) -> str:
        return code

    assert pr._kwargs_for(fn, {"code": "restore()", "nope": 1}) == {"code": "restore()"}


def test_uefn_inverse_posts_send_command(monkeypatch) -> None:
    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "backend.bridge.send_command",
        lambda command, params=None, timeout=None: posted.append((command, dict(params or {}))),
    )
    pr.post_inverse_step("set_actor_label", {"actor_path": "/a", "label": "Old"}, program="uefn")
    assert posted == [("set_actor_label", {"actor_path": "/a", "label": "Old"})]


def test_uefn_offline_when_listener_health_is_down(monkeypatch) -> None:
    monkeypatch.setattr("backend.bridge.listener_get_health", lambda *a, **k: None)
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: 4200)
    assert "offline" in pr.program_offline_reason("uefn").lower()
    assert pr.program_offline_reason("file") == ""


def test_blender_offline_uses_connection_hook(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.uefn_plugins.host.plugin_connection_for_program",
        lambda prog: {"online": False, "detail": "Offline · open Blender"} if prog == "blender" else None,
    )
    assert "open Blender" in pr.program_offline_reason("blender")


def test_blender_offline_when_status_says_disconnected(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.uefn_plugins.host.plugin_connection_for_program",
        lambda prog: None,
    )
    monkeypatch.setattr(
        pr,
        "call_plugin_tool",
        lambda name, params: {"connected": False} if name == "blender_status" else None,
    )

    class _Tool:
        pass

    class _Mgr:
        def get_tool(self, name):
            return _Tool() if name == "blender_status" else None

    class _Mcp:
        _tool_manager = _Mgr()

    monkeypatch.setattr("backend.server.mcp", _Mcp())
    assert "disconnected" in pr.program_offline_reason("blender").lower()


def test_blender_inverse_does_not_touch_uefn(monkeypatch) -> None:
    posted: list = []
    plugin: list = []
    monkeypatch.setattr(
        "backend.bridge.send_command",
        lambda *a, **k: posted.append(a),
    )
    monkeypatch.setattr(pr, "call_plugin_tool", lambda name, params: plugin.append((name, dict(params))))
    pr.post_inverse_step("blender_execute_blender_code", {"code": "restore()"}, program="blender")
    assert posted == []
    assert plugin == [("blender_execute_blender_code", {"code": "restore()"})]
