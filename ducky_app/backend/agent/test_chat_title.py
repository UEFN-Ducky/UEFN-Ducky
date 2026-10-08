"""Auto role naming for a ducky's first message."""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    "title",
    ["", "   ", "New ducky", "new ducky1", "NewDucky1", "New ducky12", "Chat", "sub-agent 3"],
)
def test_placeholder_titles(title):
    from frontend.ui_web.project_chats import is_placeholder_title

    assert is_placeholder_title(title)


@pytest.mark.parametrize("title", ["Boss Fight", "Level Designer", "New ducky pen", "Ducky Wrangler"])
def test_real_titles_are_kept(title):
    from frontend.ui_web.project_chats import is_placeholder_title

    assert not is_placeholder_title(title)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Level Designer.", "Level Designer"),
        ('  "NPC VFX Artist"  ', "NPC VFX Artist"),
        ("**Verse Gameplay Engineer**", "Verse Gameplay Engineer"),
        ("ui programmer", "Ui Programmer"),
        ("Level Designer\nBecause you asked about blockout.", "Level Designer"),
        ("One Two Three Four Five Six", "One Two Three Four"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_sanitize_role_title(raw, expected):
    from backend.agent.chat_title import sanitize_role_title

    assert sanitize_role_title(raw) == expected



def _new_conv(root: str, title: str):
    from frontend.ui_web.project_chats import create_conversation
    return create_conversation(title=title, project_root=root)


def test_first_turn_names_itself_without_an_extra_model(isolated_appdata, tmp_path, monkeypatch):
    import json
    from backend.agent.chat_title import require_self_name, self_naming_instruction, start_auto_title
    from backend.tools.panel.ducky_panel import ducky_rename_self
    from backend.workspace import identity
    from frontend.settings import PanelSettings
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    root = str(tmp_path / "TitleProj")
    (tmp_path / "TitleProj").mkdir()
    settings = PanelSettings.load()
    settings.uefn_project_root = root
    settings.save()
    conv = _new_conv(root, "New ducky2")
    monkeypatch.setattr("frontend.ui_web.agent_modes.notify_chats_changed", lambda **_kw: None)
    assert start_auto_title(conv, "fix chat questions", project_root=root) == ""
    assert load_conversation(conv.id).title == "New ducky2"
    assert "FIRST tool call" in self_naming_instruction(conv.title, conv.id)
    with pytest.raises(ValueError, match="ducky_rename_self first"):
        require_self_name("workspace_read_file", conv.id)
    require_self_name("ducky_rename_self", conv.id)
    token = identity.bind(identity.RunContext(conv_id=conv.id, run_id="name-test"))
    try:
        result = json.loads(ducky_rename_self("Chat Lifecycle Engineer"))
    finally:
        identity.reset(token)
    assert result["id"] == conv.id
    assert load_conversation(conv.id).title == "Chat Lifecycle Engineer"
    # The turn was started with a stale placeholder object; later saves keep the chosen name.
    save_conversation(conv, root)
    assert load_conversation(conv.id).title == "Chat Lifecycle Engineer"
    require_self_name("workspace_read_file", conv.id)
    assert self_naming_instruction("New ducky2", conv.id) == ""


def test_manual_names_and_disabled_auto_naming_are_preserved(isolated_appdata, tmp_path):
    from backend.agent.chat_title import require_self_name, self_naming_instruction
    from frontend.settings import PanelSettings
    assert self_naming_instruction("Boss Fight") == ""
    settings = PanelSettings.load()
    settings.chat_auto_title = False
    settings.save()
    assert self_naming_instruction("New ducky2") == ""
    require_self_name("workspace_read_file", "unknown-chat")
