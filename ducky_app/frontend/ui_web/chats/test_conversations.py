"""Conversations: create, load, titles, placement held across a stale save, delete."""

from __future__ import annotations

import pytest

from frontend.settings import PanelSettings
from frontend.ui_web.chats import conversations as convs
from frontend.ui_web.chats.saves import flush_chat_saves, save_conversation


@pytest.fixture(autouse=True)
def _flushed():
    yield
    flush_chat_saves()


def test_placeholder_titles_count_as_unnamed() -> None:
    for title in ("", "New ducky", "NewDucky1", "chat 3", "Group", "sub-agent2"):
        assert convs.is_placeholder_title(title), title
    assert not convs.is_placeholder_title("Verse Coder")


def test_a_new_chat_loads_back_with_its_title(tmp_path) -> None:
    root = str(tmp_path)
    conv = convs.create_conversation(PanelSettings.load(), "", title="Builder", project_root=root)
    assert convs.conversation_title(conv.id, root) == "Builder"
    meta = convs.load_conversation_meta(conv.id, root)
    assert meta is not None and meta.messages == []
    assert conv.id in {c.id for c in convs.list_all_conversation_metadata(root)}


def test_auto_title_only_replaces_a_placeholder(tmp_path) -> None:
    root = str(tmp_path)
    conv = convs.create_conversation(PanelSettings.load(), "", project_root=root)
    assert convs.auto_title(conv, "Fix the lobby", root) is True
    assert convs.auto_title(conv, "Something else", root) is False
    assert convs.retitle_if_unchanged(conv.id, "Fix the lobby", "Lobby fix", root) is True
    assert convs.retitle_if_unchanged(conv.id, "Fix the lobby", "Again", root) is False
    assert convs.load_conversation(conv.id, root).title == "Lobby fix"


def test_a_stale_copy_never_undoes_a_rename(tmp_path) -> None:
    root = str(tmp_path)
    conv = convs.create_conversation(PanelSettings.load(), "", title="Before", project_root=root)
    stale = convs.load_conversation(conv.id, root)
    convs.rename_conversation(conv.id, "After", root)
    stale.messages = [{"role": "user", "content": "hi"}]
    save_conversation(stale, root, wait=True)
    assert convs.load_conversation(conv.id, root).title == "After"


def test_delete_takes_the_chat_and_its_sub_agents(tmp_path) -> None:
    root = str(tmp_path)
    settings = PanelSettings.load()
    parent = convs.create_conversation(settings, "", title="Lead", project_root=root)
    child = convs.create_conversation(settings, "", title="Helper", parent_conv_id=parent.id, project_root=root)
    convs.delete_conversation(parent.id, root)
    assert convs.load_conversation(parent.id, root) is None
    assert convs.load_conversation(child.id, root) is None
