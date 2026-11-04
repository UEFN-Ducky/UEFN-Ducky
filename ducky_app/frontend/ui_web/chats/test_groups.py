"""Group hubs: legacy hubs get a folder, archived hubs stay put, descendants and islands."""

from __future__ import annotations

from frontend.archive_folder import ARCHIVE_FOLDER_ID
from frontend.settings import PanelSettings
from frontend.ui_web.chats.conversations import create_conversation, load_conversation
from frontend.ui_web.chats.folders import create_folder, load_folders, save_folders
from frontend.ui_web.chats.groups import (
    conversation_descendant_ids,
    ensure_group_folder_hubs,
    group_hub_ids_in,
    island_for_conversation,
)
from frontend.ui_web.chats.saves import save_conversation


def test_ensure_group_folder_hubs_wraps_legacy_group(tmp_path):
    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), "", title="Squad", project_root=root)
    conv.is_group = True
    conv.group_members = []
    save_conversation(conv, root)

    assert ensure_group_folder_hubs(root) == 1
    hubs = [f for f in load_folders(root) if (getattr(f, "group_hub_id", "") or "") == conv.id]
    assert len(hubs) == 1
    assert hubs[0].name == "Squad"
    fresh = load_conversation(conv.id, project_root=root)
    assert fresh is not None
    assert fresh.folder_id == hubs[0].id
    assert ensure_group_folder_hubs(root) == 0


def test_ensure_group_folder_hubs_leaves_archived_hubs_alone(tmp_path):
    """A deleted group's hub must not come back as a root folder on the next load."""
    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), ARCHIVE_FOLDER_ID, title="Squad", project_root=root)
    conv.is_group = True
    conv.folder_id = ARCHIVE_FOLDER_ID
    save_conversation(conv, root)

    assert ensure_group_folder_hubs(root) == 0
    assert all((getattr(f, "group_hub_id", "") or "") != conv.id for f in load_folders(root))
    fresh = load_conversation(conv.id, project_root=root)
    assert fresh is not None
    assert fresh.folder_id == ARCHIVE_FOLDER_ID


def test_group_hub_ids_in_names_only_the_group_folders(tmp_path):
    root = str(tmp_path)
    plain = create_folder("Notes", "", root)
    group = create_folder("Team", "", root, group_hub_id="hub-1")
    assert group_hub_ids_in([plain.id, group.id], root) == ["hub-1"]
    assert group_hub_ids_in([plain.id], root) == []
    rows = load_folders(root)
    save_folders(rows, root)
    assert group_hub_ids_in([group.id], root) == ["hub-1"]


def test_descendants_are_parent_first_and_the_island_lists_them(tmp_path):
    root = str(tmp_path)
    settings = PanelSettings.load()
    top = create_conversation(settings, "", title="Lead", project_root=root)
    child = create_conversation(settings, "", title="Child", parent_conv_id=top.id, project_root=root)
    grandchild = create_conversation(settings, "", title="Grandchild", parent_conv_id=child.id, project_root=root)

    assert conversation_descendant_ids(top.id, root) == [child.id, grandchild.id]
    assert conversation_descendant_ids(grandchild.id, root) == []
    convs, _folders = island_for_conversation(top.id, root)
    assert {top.id, child.id, grandchild.id} <= {c.id for c in convs}
