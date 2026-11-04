"""Chat folders: create, rename, nesting, cycles, delete and sidebar layout."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.archive_folder import ARCHIVE_FOLDER_ID
from frontend.settings import PanelSettings
from frontend.ui_web.chats import folders
from frontend.ui_web.chats.conversations import create_conversation, load_conversation


def test_the_folder_list_always_has_archive(tmp_path) -> None:
    assert any(f.id == ARCHIVE_FOLDER_ID for f in folders.load_folders(str(tmp_path)))


def test_new_folders_go_last_among_their_siblings_and_nest(tmp_path) -> None:
    root = str(tmp_path)
    first = folders.create_folder("A", "", root)
    second = folders.create_folder("B", "", root)
    inner = folders.create_folder("Inner", first.id, root)
    assert second.sort_order > first.sort_order
    assert folders.folder_subtree_ids(first.id, root) == [first.id, inner.id]
    folders.rename_folder(inner.id, "Renamed", root)
    assert next(f for f in folders.load_folders(root) if f.id == inner.id).name == "Renamed"


def test_a_folder_never_becomes_its_own_ancestor() -> None:
    rows = [SimpleNamespace(id="a", parent_id=""), SimpleNamespace(id="b", parent_id="a")]
    assert folders._would_create_cycle(rows, "a", "b") is True
    assert folders._would_create_cycle(rows, "b", "a") is False
    assert folders._would_create_cycle(rows, "a", "a") is True
    assert folders._would_create_cycle(rows, "a", "") is False


def test_deleting_a_plain_folder_keeps_its_chats_at_the_root(tmp_path) -> None:
    root = str(tmp_path)
    folder = folders.create_folder("Notes", "", root)
    chat = create_conversation(PanelSettings.load(), folder.id, title="Scratch", project_root=root)
    assert folders.delete_folder(folder.id, root) == []
    assert load_conversation(chat.id, project_root=root).folder_id == ""
    assert all(f.id != folder.id for f in folders.load_folders(root))


def test_a_sidebar_layout_skips_unknown_rows_and_moves_known_ones(tmp_path) -> None:
    root = str(tmp_path)
    folder = folders.create_folder("Box", "", root)
    chat = create_conversation(PanelSettings.load(), "", title="Mover", project_root=root)
    skipped = folders.apply_sidebar_layout(
        folders=[{"id": "missing", "parent_id": "", "sort_order": 1}],
        chats=[{"id": chat.id, "folder_id": folder.id, "sort_order": 5}, {"id": "gone", "folder_id": ""}],
        project_root=root,
    )
    assert skipped == 2
    moved = load_conversation(chat.id, project_root=root)
    assert (moved.folder_id, moved.sort_order) == (folder.id, 5.0)
