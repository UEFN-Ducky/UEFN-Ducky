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
    ("text", "expected"),
    [
        ("can you build the niagara particle burst", "VFX Artist"),
        ("make the enemy npc chase the player", "NPC AI Designer"),
        ("the water shader material looks flat", "Material Artist"),
        ("retarget this animation onto the skeleton", "Animation Engineer"),
        ("add a hud widget for the score", "UI Programmer"),
        ("write verse code for the pickup device", "Verse Programmer"),
        ("blockout the level layout for round one", "Level Designer"),
        ("import this blender mesh", "3D Modeler"),
        ("the sound effect never plays", "Audio Designer"),
        ("everything crashes when i press play", "Debug Engineer"),
        # Domain wins over the verb: a compile error is still Verse work.
        ("my project has a compile error", "Verse Programmer"),
        ("hey", "General Helper"),
    ],
)
def test_role_from_keywords(text, expected):
    from backend.agent.chat_title import role_from_keywords

    assert role_from_keywords(text) == expected


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


def test_generate_role_title_uses_sanitizer(monkeypatch):
    from backend.agent import chat_title

    monkeypatch.setattr(
        "frontend.ui_web.plugin_llm._resolve_api_model",
        lambda **_kw: ("openai", "gpt-4o-mini"),
    )
    monkeypatch.setattr(
        "backend.agent.batch_backends.supports_batch_complete", lambda _p: True
    )

    async def fake_complete(**_kw):
        return "  Level Designer.  "

    monkeypatch.setattr("frontend.ui_web.plugin_llm._complete_text", fake_complete)

    assert chat_title.generate_role_title("blockout the level") == "Level Designer"


def test_generate_role_title_skips_cli_only_backends(monkeypatch):
    from backend.agent import chat_title

    monkeypatch.setattr(
        "frontend.ui_web.plugin_llm._resolve_api_model", lambda **_kw: ("cursor", "auto")
    )
    monkeypatch.setattr(
        "backend.agent.batch_backends.supports_batch_complete", lambda _p: False
    )

    assert chat_title.generate_role_title("blockout the level") == ""


def _new_conv(root: str, title: str):
    from frontend.ui_web.project_chats import create_conversation, load_conversation, rename_conversation

    conv = create_conversation(project_root=root)
    rename_conversation(conv.id, title, project_root=root)
    return load_conversation(conv.id, root)


def test_start_auto_title_renames_placeholder(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent.chat_title import start_auto_title
    from frontend.ui_web.project_chats import load_conversation

    root = str(tmp_path / "TitleProj")
    (tmp_path / "TitleProj").mkdir()
    notified: list[bool] = []
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.notify_chats_changed",
        lambda **_kw: notified.append(True),
    )

    conv = _new_conv(root, "NewDucky1")
    applied = start_auto_title(conv, "blockout the level layout", project_root=root)

    assert applied == "Level Designer"
    assert conv.title == "Level Designer"
    assert (load_conversation(conv.id, root).title or "") == "Level Designer"
    assert notified


def test_start_auto_title_keeps_user_name(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent.chat_title import start_auto_title
    from frontend.ui_web.project_chats import load_conversation

    root = str(tmp_path / "TitleProj2")
    (tmp_path / "TitleProj2").mkdir()
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.notify_chats_changed", lambda **_kw: None
    )

    conv = _new_conv(root, "Boss Fight")

    assert start_auto_title(conv, "blockout the level layout", project_root=root) == ""
    assert (load_conversation(conv.id, root).title or "") == "Boss Fight"


def test_start_auto_title_respects_toggle(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent.chat_title import start_auto_title
    from frontend.settings import PanelSettings
    from frontend.ui_web.project_chats import load_conversation

    root = str(tmp_path / "TitleProjOff")
    (tmp_path / "TitleProjOff").mkdir()
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.notify_chats_changed", lambda **_kw: None
    )
    s = PanelSettings.load()
    s.chat_auto_title = False
    s.save()

    conv = _new_conv(root, "NewDucky1")

    assert start_auto_title(conv, "blockout the level layout", project_root=root) == ""
    assert (load_conversation(conv.id, root).title or "") == "NewDucky1"


def test_refine_updates_live_object_and_disk(isolated_appdata, tmp_path, monkeypatch):
    """The turn keeps saving the live conv, so the refined title must land on it too."""
    from backend.agent import chat_title
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    root = str(tmp_path / "TitleProj3")
    (tmp_path / "TitleProj3").mkdir()
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.notify_chats_changed", lambda **_kw: None
    )
    monkeypatch.setattr(chat_title, "generate_role_title", lambda *_a, **_kw: "Blockout Specialist")

    conv = _new_conv(root, "Level Designer")
    chat_title._refine(conv, "blockout the level", "Level Designer", "openai:x", root, None)

    assert conv.title == "Blockout Specialist"
    assert (load_conversation(conv.id, root).title or "") == "Blockout Specialist"

    # A later save from the streaming turn must not resurrect the provisional title.
    save_conversation(conv, root)
    assert (load_conversation(conv.id, root).title or "") == "Blockout Specialist"


def test_refine_skips_manual_rename(isolated_appdata, tmp_path, monkeypatch):
    from backend.agent import chat_title
    from frontend.ui_web.project_chats import load_conversation, rename_conversation

    root = str(tmp_path / "TitleProj4")
    (tmp_path / "TitleProj4").mkdir()
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.notify_chats_changed", lambda **_kw: None
    )
    monkeypatch.setattr(chat_title, "generate_role_title", lambda *_a, **_kw: "Blockout Specialist")

    conv = _new_conv(root, "Level Designer")
    rename_conversation(conv.id, "Boss Fight", project_root=root)

    chat_title._refine(conv, "blockout the level", "Level Designer", "openai:x", root, None)

    assert (load_conversation(conv.id, root).title or "") == "Boss Fight"
    # The live object must stay stale too, or the next turn save clobbers the rename.
    assert conv.title == "Level Designer"
