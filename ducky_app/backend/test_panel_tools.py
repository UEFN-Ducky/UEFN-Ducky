"""Panel-pathway tools: settings patch, UI-RPC broker, MCP apply merge safety."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    """Point settings storage at a temp dir so tests never touch real AppData."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_apply_settings_patch_roundtrip(isolated_appdata):
    from backend.tools.panel_settings import apply_settings_patch
    from frontend.settings import PanelSettings

    out = apply_settings_patch({"tool_result_format": "json"})
    assert "error" not in out
    assert out["after"]["tool_result_format"] == "json"
    assert PanelSettings.load().tool_result_format == "json"


def test_apply_settings_patch_dry_run_does_not_persist(isolated_appdata):
    from backend.tools.panel_settings import apply_settings_patch
    from frontend.settings import PanelSettings

    out = apply_settings_patch({"tool_result_format": "json"}, dry_run=True)
    assert out["dry_run"] is True
    assert out["after"]["tool_result_format"] == "json"
    assert PanelSettings.load().tool_result_format == "toon"


def test_apply_settings_patch_rejects_unknown_key(isolated_appdata):
    from backend.tools.panel_settings import apply_settings_patch

    out = apply_settings_patch({"appearance_foundation": {"accent": "#fff"}})
    assert "error" in out  # structured field is not `settable`


def test_apply_settings_patch_enum_validation(isolated_appdata):
    from backend.tools.panel_settings import apply_settings_patch

    out = apply_settings_patch({"tool_result_format": "yaml"})
    assert "error" in out


def test_apply_settings_patch_gated_by_master_switch(isolated_appdata):
    from backend.tools.panel_settings import apply_settings_patch

    assert "error" not in apply_settings_patch({"allow_settings_write": False})
    blocked = apply_settings_patch({"tool_result_format": "json"})
    assert "error" in blocked


def test_ui_rpc_broker_submit_respond_wait():
    from frontend.ui_web import ui_rpc

    request_id, event = ui_rpc.submit("spotlight", {"target_id": "x"})
    assert event["type"] == "ui_rpc_request"
    assert event["request_id"] == request_id
    # Not answered yet → wait returns None (pending).
    assert ui_rpc.wait(request_id, 0.01) is None
    assert ui_rpc.respond(request_id, {"clicked": True}) is True
    assert ui_rpc.wait(request_id, 1.0) == {"clicked": True}
    # Unknown / already-consumed id.
    assert ui_rpc.respond("nope", {}) is False


def test_mcp_upsert_refuses_reserved_id(isolated_appdata):
    from backend.tools.panel_mcp import ducky_mcp_upsert_server

    result = json.loads(ducky_mcp_upsert_server("uefn", {"command": "x"}))
    assert "reserved" in (result.get("error") or "")


def test_mcp_validate_ok_for_stdio(isolated_appdata):
    from backend.tools.panel_mcp import ducky_mcp_validate

    result = json.loads(ducky_mcp_validate(config={"transport": "stdio", "command": "uvx"}))
    assert result["ok"] is True
    assert result["transport"] == "stdio"


def test_apply_merge_preserves_other_mcp_servers(tmp_path):
    # ducky_mcp_apply relies on merge_uefn_into_config never dropping other servers.
    from frontend.merge import merge_uefn_into_config

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"other_tool": {"command": "other-mcp"}}}),
        encoding="utf-8",
    )
    merge_uefn_into_config(config, {"type": "stdio", "command": "uefn.exe", "args": []})
    root = json.loads(config.read_text(encoding="utf-8"))
    assert root["mcpServers"]["other_tool"] == {"command": "other-mcp"}
    assert root["mcpServers"]["uefn"]["command"] == "uefn.exe"
