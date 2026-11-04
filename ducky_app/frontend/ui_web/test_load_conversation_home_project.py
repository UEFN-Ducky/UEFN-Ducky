"""Opening or saving a chat through any project keeps it where it is kept: the one list,
or (an older Ducky's row) its project."""

from __future__ import annotations

import tempfile
from pathlib import Path

from frontend.settings import PanelSettings
from frontend.ui_web.chats.conversations import conversation_path, create_conversation, load_conversation
from frontend.ui_web.chats.folders import move_chats_to_project
from frontend.ui_web.chats.groups import adopt_group_project
from frontend.ui_web.chats.saves import save_conversation
from frontend.ui_web.chats.store import ONE_LIST_SLUG, _use_db, project_slug


def test_load_and_save_keep_the_chat_in_the_one_list():
    with tempfile.TemporaryDirectory() as tmp:
        home = str(Path(tmp) / "HomeIsland")
        other = str(Path(tmp) / "OtherIsland")
        Path(home).mkdir()
        Path(other).mkdir()
        conv = create_conversation(PanelSettings.load(), "", title="Away", project_root=home)
        conv.messages = [{"role": "user", "text": "hi", "content": "hi", "ts": 1}]
        save_conversation(conv, home)

        loaded = load_conversation(conv.id, project_root=other)
        assert loaded is not None
        assert loaded.title == "Away"
        loaded.title = "Still away"
        save_conversation(loaded, other)

        home_again = load_conversation(conv.id, project_root=home)
        assert home_again is not None
        assert home_again.title == "Still away"
        if _use_db():
            from frontend.ui_web.chats.store import _repo

            assert _repo().conv_project_id(conv.id) == ONE_LIST_SLUG
        else:
            assert conversation_path(conv.id, home) == conversation_path(conv.id, other)
            assert conversation_path(conv.id, "").is_file()


def test_adopt_group_project_moves_member_onto_the_group_island():
    if not _use_db():
        return
    with tempfile.TemporaryDirectory() as tmp:
        home = str(Path(tmp) / "HomeIsland")
        Path(home).mkdir()
        group = create_conversation(PanelSettings.load(), "", title="Group")
        group.is_group = True
        save_conversation(group)
        move_chats_to_project([group.id], [], project_slug(home), "")  # an older Ducky's group
        member = create_conversation(PanelSettings.load(), "", title="Coder")
        adopt_group_project(member, group.id)
        from frontend.ui_web.chats.store import _repo

        assert _repo().conv_project_id(member.id) == project_slug(home)


def test_sync_rehomes_a_member_invited_from_another_island():
    if not _use_db():
        return
    from frontend.ui_web.group_orchestrator import sync_group_members_from_folder
    from frontend.ui_web.chats.folders import create_folder, load_folders, save_folders
    from frontend.ui_web.chats.store import _repo

    with tempfile.TemporaryDirectory() as tmp:
        home = str(Path(tmp) / "HomeIsland")
        Path(home).mkdir()
        group = create_conversation(PanelSettings.load(), "", title="Group")
        group.is_group = True
        folder = create_folder("Group1")
        folders = load_folders()
        for item in folders:
            if item.id == folder.id:
                item.group_hub_id = group.id
        save_folders(folders)
        group.folder_id = folder.id
        save_conversation(group)
        member = create_conversation(PanelSettings.load(), "", title="Coder")
        # An older Ducky's group, kept under its island; the member was made in the one list.
        move_chats_to_project([], [folder.id], project_slug(home), "")
        _repo().move_to_project(ONE_LIST_SLUG, [], [(member.id, folder.id)])
        member = load_conversation(member.id)
        member.parent_conv_id = group.id
        save_conversation(member)
        group = load_conversation(group.id)
        group.group_members = [{"member_conv_id": member.id, "name": "Coder"}]
        save_conversation(group)

        rows = sync_group_members_from_folder(group)

        assert any(row.get("member_conv_id") == member.id for row in rows)
        assert _repo().conv_project_id(member.id) == project_slug(home)
