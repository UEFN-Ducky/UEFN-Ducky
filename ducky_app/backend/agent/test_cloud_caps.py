"""Cloud deny list is a ceiling on list_mcp_tools."""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent.tools import apply_cloud_tool_deny


def test_apply_cloud_tool_deny_drops_denied(monkeypatch) -> None:
    monkeypatch.setattr(
        "frontend.duckyos_account.cloud_denied_names",
        lambda: {"denied_me"},
    )
    monkeypatch.setattr("frontend.duckyos_account.tool_blocked_by_caps", lambda _n: None)
    tools = [
        SimpleNamespace(name="keep_me"),
        SimpleNamespace(name="denied_me"),
    ]
    out = apply_cloud_tool_deny(tools)
    assert [t.name for t in out] == ["keep_me"]


def test_apply_cloud_tool_deny_empty_is_noop(monkeypatch) -> None:
    monkeypatch.setattr("frontend.duckyos_account.cloud_denied_names", lambda: set())
    monkeypatch.setattr("frontend.duckyos_account.tool_blocked_by_caps", lambda _n: None)
    tools = [SimpleNamespace(name="keep_me")]
    assert apply_cloud_tool_deny(tools)[0].name == "keep_me"


def test_apply_cloud_tool_deny_hides_other_programs(monkeypatch) -> None:
    monkeypatch.setattr("frontend.duckyos_account.cloud_denied_names", lambda: set())
    monkeypatch.setattr(
        "frontend.duckyos_account.tool_blocked_by_caps",
        lambda name: "off" if name.startswith("blender_") else None,
    )
    tools = [
        SimpleNamespace(name="workspace_read_file"),
        SimpleNamespace(name="blender_status"),
    ]
    assert [t.name for t in apply_cloud_tool_deny(tools)] == ["workspace_read_file"]
