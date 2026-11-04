"""tool_captures — persist screenshots without base64 in MCP results."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.ui_web.tool_captures import (
    build_tool_capture_url,
    resolve_tool_capture_path,
    save_capture_for_agents,
    save_tool_capture_png,
    tool_captures_dir,
)

import base64

# Minimal 1x1 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_save_tool_capture_png_returns_media_url_not_base64(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.resolve_app_data_dir",
        lambda for_write=False: tmp_path,
    )
    saved = save_tool_capture_png(_PNG, prefix="blender_viewport")
    assert "base64" not in saved
    assert saved["bytes"] == len(_PNG)
    assert Path(str(saved["path"])).is_file()
    assert str(saved["media_url"]).startswith("http://127.0.0.1:")
    assert "/tool-captures/" in str(saved["media_url"])
    name = str(saved["filename"])
    resolved = resolve_tool_capture_path(name)
    assert resolved.read_bytes() == _PNG
    assert build_tool_capture_url(name).endswith(name)


def test_resolve_tool_capture_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.resolve_app_data_dir",
        lambda for_write=False: tmp_path,
    )
    tool_captures_dir(for_write=True)
    with pytest.raises(ValueError):
        resolve_tool_capture_path("../secret.png")
    with pytest.raises(ValueError):
        resolve_tool_capture_path("not-a-png.txt")


def test_save_capture_for_agents_writes_the_chat_folder_only(tmp_path, monkeypatch):
    """Screenshots land in the chat attachments folder, never the project or tool_captures."""
    from backend.workspace.identity import RunContext, bind, reset

    project = tmp_path / "island"
    project.mkdir()

    class _Settings:
        uefn_project_root = str(project)

    monkeypatch.setattr(
        "frontend.settings.PanelSettings.load",
        staticmethod(lambda: _Settings()),
    )
    monkeypatch.setattr(
        "frontend.ui_web.chats.store._chats_root",
        lambda: tmp_path / "chats" / "projects",
    )
    token = bind(RunContext(conv_id="chat-1"))
    try:
        saved = save_capture_for_agents(_PNG, prefix="uefn_viewport")
    finally:
        reset(token)
    assert saved["ok"] is True
    path = Path(str(saved["path"]))
    assert path.is_file()
    assert path.read_bytes() == _PNG
    assert "/conversations/chat-1/attachments/" in str(path).replace("\\", "/")
    assert "tool_captures" not in str(path).replace("\\", "/")
    assert "DuckyCaptures" not in str(path)
    assert not (project / "Saved").exists()
    assert not list(project.rglob("*.png"))
    monkeypatch.delenv("DUCKY_CONV_ID", raising=False)
    refused = save_capture_for_agents(_PNG, prefix="uefn_viewport")
    assert refused["ok"] is False
    assert "no active chat" in str(refused["error"])
    # Dedicated bridge: nothing bound, chat id only in the process env.
    monkeypatch.setenv("DUCKY_CONV_ID", "chat-env")
    from_env = save_capture_for_agents(_PNG, prefix="uefn_viewport")
    assert from_env["ok"] is True
    assert "/conversations/chat-env/attachments/" in str(from_env["path"]).replace("\\", "/")
