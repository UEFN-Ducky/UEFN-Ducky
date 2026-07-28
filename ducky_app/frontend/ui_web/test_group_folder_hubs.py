"""Group folders: legacy hubs migrate; delete folder archives hub + members."""

from __future__ import annotations

import tempfile
from pathlib import Path

from frontend.archive_folder import ARCHIVE_FOLDER_ID
from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import (
    create_conversation,
    create_folder,
    delete_folder,
    ensure_group_folder_hubs,
    list_conversations,
    load_conversation,
    load_folders,
    save_conversation,
    save_folders,
)


def test_ensure_group_folder_hubs_wraps_legacy_group():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        conv = create_conversation(settings, "", title="Squad", project_root=root)
        conv.is_group = True
        conv.group_members = []
        save_conversation(conv, root)

        assert ensure_group_folder_hubs(root) == 1
        folders = load_folders(root)
        hubs = [f for f in folders if (getattr(f, "group_hub_id", "") or "") == conv.id]
        assert len(hubs) == 1
        assert hubs[0].name == "Squad"
        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        assert fresh.folder_id == hubs[0].id
        assert ensure_group_folder_hubs(root) == 0


def test_delete_group_folder_archives_members():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder = create_folder("Squad", "", root)
        hub = create_conversation(settings, folder.id, title="Squad", project_root=root)
        hub.is_group = True
        hub.group_members = []
        save_conversation(hub, root)
        folders = load_folders(root)
        for f in folders:
            if f.id == folder.id:
                f.group_hub_id = hub.id
                break
        save_folders(folders, root)

        member = create_conversation(
            settings,
            folder.id,
            title="Verse Coder",
            parent_conv_id=hub.id,
            project_root=root,
        )
        delete_folder(folder.id, root)

        archived_hub = load_conversation(hub.id, project_root=root)
        archived_member = load_conversation(member.id, project_root=root)
        assert archived_hub is not None
        assert archived_member is not None
        assert archived_hub.folder_id == ARCHIVE_FOLDER_ID
        assert archived_member.folder_id == ARCHIVE_FOLDER_ID
        assert archived_member.parent_conv_id == hub.id
        assert all(f.id != folder.id for f in load_folders(root))
        assert {c.id for c in list_conversations(project_root=root)} >= {hub.id, member.id}
