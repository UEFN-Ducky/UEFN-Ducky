"""A window holding a stale Duckies tree must never bring deleted chats or groups back.

Layout saves skip ids the project no longer has (never create them), never move a chat
into or out of Archive, and deletes tell every window which rows to drop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.settings import PanelSettings
from frontend.ui_web import panel_api as pa
from frontend.ui_web import project_chats as pc
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


@pytest.fixture(params=["db", "files"])
def backend(request, monkeypatch) -> str:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", request.param)
    return request.param


@pytest.fixture
def pushes(monkeypatch) -> list[dict]:
    """Capture chats_changed pushes instead of sending them to a panel."""
    sent: list[dict] = []

    def fake_notify(conv_id: str = "", title: str = "", folder_id: str = "", **kwargs) -> None:
        sent.append({"conv_id": conv_id, **kwargs})

    monkeypatch.setattr(pa, "notify_chats_changed", fake_notify)
    return sent


def _island(tmp_path: Path, name: str = "Island") -> str:
    root = tmp_path / name
    (root / "Content").mkdir(parents=True)
    return str(root)


def _open_island(monkeypatch, root: str) -> None:
    settings = PanelSettings.load()
    settings.uefn_project_root = root
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls, **_kw: settings))


def _group(root: str, name: str = "Team test r9"):
    folder = pc.create_folder(name, project_root=root)
    hub = pc.create_conversation(title=name, folder_id=folder.id, project_root=root)
    hub.is_group = True
    pc.save_conversation(hub, root)
    rows = pc.load_folders(root)
    for row in rows:
        if row.id == folder.id:
            row.group_hub_id = hub.id
    pc.save_folders(rows, root)
    member = pc.create_conversation(title="Member", folder_id=folder.id, project_root=root)
    member.parent_conv_id = hub.id
    pc.save_conversation(member, root)
    return folder, hub, member


def test_unknown_ids_are_skipped_not_created(backend: str, tmp_path: Path) -> None:
    root = _island(tmp_path)
    keep = pc.create_conversation(title="Keep", project_root=root)
    other = pc.create_conversation(title="Other", project_root=root)

    skipped = pc.apply_sidebar_layout(
        folders=[{"id": "ghost-folder", "parent_id": "", "sort_order": 0}],
        chats=[
            {"id": "ghost-chat", "folder_id": "", "sort_order": 0},
            {"id": other.id, "folder_id": "", "sort_order": 0},
            {"id": keep.id, "folder_id": "ghost-folder", "sort_order": 1},
        ],
        project_root=root,
    )

    assert skipped == 3  # the folder, the chat, and the move into the missing folder
    assert pc.load_conversation("ghost-chat", root) is None
    assert "ghost-folder" not in {folder.id for folder in pc.load_folders(root)}
    assert (pc.load_conversation(other.id, root).sort_order) == 0
    assert (pc.load_conversation(keep.id, root).folder_id or "") == ""


def test_stale_layout_never_recreates_a_deleted_group(backend: str, tmp_path: Path) -> None:
    root = _island(tmp_path)
    folder, hub, member = _group(root)
    # The window still shows the group when another window (or an agent) deletes it.
    stale_folders = [{"id": folder.id, "parent_id": "", "sort_order": 0}]
    stale_chats = [{"id": member.id, "folder_id": folder.id, "sort_order": 0}]
    pc.delete_folder(folder.id, root, archive_members=True)
    assert folder.id not in {f.id for f in pc.load_folders(root)}

    pc.apply_sidebar_layout(folders=stale_folders, chats=stale_chats, project_root=root)

    assert folder.id not in {f.id for f in pc.load_folders(root)}
    hub_now = pc.load_conversation(hub.id, root)
    assert hub_now is not None and pc.is_archive_folder_id(hub_now.folder_id)
    member_now = pc.load_conversation(member.id, root)
    assert member_now is not None and pc.is_archive_folder_id(member_now.folder_id)


def test_stale_layout_never_recreates_a_deleted_chat(backend: str, tmp_path: Path, monkeypatch) -> None:
    root = _island(tmp_path)
    gone = pc.create_conversation(title="Gone", project_root=root)
    folder = pc.create_folder("Box", project_root=root)
    stale = pc.load_conversation(gone.id, root)
    pc.list_conversations(project_root=root)  # one-time sort migration out of the way
    pc.delete_conversation(gone.id, root)
    assert pc.load_conversation(gone.id, root) is None

    # Worst case: the chat list was read just before the delete landed.
    real_load = pc._load_all_conversations
    monkeypatch.setattr(pc, "_load_all_conversations", lambda *a, **k: [*real_load(*a, **k), stale])
    pc.apply_sidebar_layout(
        folders=[],
        chats=[{"id": gone.id, "folder_id": folder.id, "sort_order": 3}],
        project_root=root,
    )

    assert pc.load_conversation(gone.id, root) is None


def test_layout_never_moves_chats_in_or_out_of_archive(backend: str, tmp_path: Path) -> None:
    root = _island(tmp_path)
    archived = pc.create_conversation(title="Archived", project_root=root)
    active = pc.create_conversation(title="Active", project_root=root)
    pc.move_conversation(archived.id, pc.ARCHIVE_FOLDER_ID, root)

    pc.apply_sidebar_layout(
        folders=[],
        chats=[
            {"id": archived.id, "folder_id": "", "sort_order": 0},
            {"id": active.id, "folder_id": pc.ARCHIVE_FOLDER_ID, "sort_order": 1},
        ],
        project_root=root,
    )

    assert pc.is_archive_folder_id(pc.load_conversation(archived.id, root).folder_id)
    assert (pc.load_conversation(active.id, root).folder_id or "") == ""


def test_moves_inside_a_project_still_save(backend: str, tmp_path: Path) -> None:
    root = _island(tmp_path)
    box = pc.create_folder("Box", project_root=root)
    shelf = pc.create_folder("Shelf", project_root=root)
    chat = pc.create_conversation(title="Chat", project_root=root)

    pc.apply_sidebar_layout(
        folders=[
            {"id": shelf.id, "parent_id": "", "sort_order": 0},
            {"id": box.id, "parent_id": shelf.id, "sort_order": 0},
        ],
        chats=[{"id": chat.id, "folder_id": box.id, "sort_order": 0}],
        project_root=root,
    )

    rows = {f.id: f for f in pc.load_folders(root)}
    assert rows[box.id].parent_id == shelf.id
    assert pc.load_conversation(chat.id, root).folder_id == box.id
    # A cycle from a stale window is skipped, the tree stays a tree.
    pc.apply_sidebar_layout(folders=[{"id": shelf.id, "parent_id": box.id, "sort_order": 0}], chats=[], project_root=root)
    rows = {f.id: f for f in pc.load_folders(root)}
    assert (rows[shelf.id].parent_id or "") == ""


def test_global_agents_layout_saves_to_the_no_island_bucket(backend: str, tmp_path: Path, monkeypatch, pushes) -> None:
    island = _island(tmp_path)
    _open_island(monkeypatch, island)
    box = pc.create_folder("Global box", project_root="")
    loose = pc.create_conversation(title="Loose", project_root="")

    PanelApiChatsMixin().apply_sidebar_layout(
        {
            "folders": [{"id": box.id, "parent_id": "", "sort_order": 0}],
            "chats": [{"id": loose.id, "folder_id": box.id, "sort_order": 0}],
            "project_slug": "_no_project",
        }
    )

    assert pc.load_conversation(loose.id, "").folder_id == box.id
    assert pc.conversation_project_slug(loose.id) == "_no_project"
    assert pushes and pushes[-1].get("open_tab") is False


def test_layout_for_an_unknown_island_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(pc, "project_root_for_slug", lambda slug: None)
    with pytest.raises(ValueError, match="Unknown project"):
        PanelApiChatsMixin._layout_project_root("Nowhere_0123456789abcdef")
    assert PanelApiChatsMixin._layout_project_root("") is None


def test_group_create_in_global_agents(backend: str, tmp_path: Path, monkeypatch, pushes) -> None:
    island = _island(tmp_path)
    _open_island(monkeypatch, island)

    res = PanelApiChatsMixin().group_create("Crew", "", False, "_no_project")

    assert res["ok"] and res["folder_id"]
    folders = {f.id: f for f in pc.load_folders("")}
    assert folders[res["folder_id"]].group_hub_id == res["id"]
    assert pc.conversation_project_slug(res["id"]) == "_no_project"
    # One list: naming the open island shows the same folders.
    assert res["folder_id"] in {f.id for f in pc.load_folders(island)}


def test_deletes_tell_every_window_which_rows_to_drop(backend: str, tmp_path: Path, monkeypatch, pushes) -> None:
    island = _island(tmp_path)
    _open_island(monkeypatch, island)
    monkeypatch.setattr("frontend.ui_web.agent_modes.is_agent_running", lambda _cid: False)
    api = PanelApiChatsMixin()
    folder, hub, member = _group(island)
    chat = pc.create_conversation(title="Chat", project_root=island)
    doomed = pc.create_conversation(title="Doomed", project_root=island)

    api.move_conversation(chat.id, pc.ARCHIVE_FOLDER_ID)
    assert pushes[-1]["removed_conv_ids"] == [chat.id]
    api.delete_conversation(doomed.id)
    assert pushes[-1]["removed_conv_ids"] == [doomed.id]
    api.delete_folder(folder.id, True)
    assert pushes[-1]["removed_folder_ids"] == [folder.id]
    assert hub.id in pushes[-1]["removed_conv_ids"]
    assert member.id in pushes[-1]["removed_conv_ids"]


def test_a_late_save_never_brings_a_deleted_chat_back(tmp_path: Path, monkeypatch, pushes) -> None:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "db")
    island = _island(tmp_path)
    _open_island(monkeypatch, island)
    chat = pc.create_conversation(title="Doomed", project_root=island)
    held = pc.load_conversation(chat.id, island)  # a running turn still holds the chat

    PanelApiChatsMixin().delete_conversation(chat.id)
    held.title = "Turn finished"
    pc.save_conversation(held, island)

    assert pc.load_conversation(chat.id, island) is None
    assert chat.id not in {c.id for c in pc.list_conversations("", island)}


def test_a_stale_folder_list_never_brings_a_deleted_group_back(tmp_path: Path, monkeypatch, pushes) -> None:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "db")
    island = _island(tmp_path)
    _open_island(monkeypatch, island)
    folder, _hub, _member = _group(island)
    keep = pc.create_folder("Keep", project_root=island)
    stale = pc.load_folders(island)  # read before the delete

    PanelApiChatsMixin().delete_folder(folder.id, True)
    pc.save_folders(stale, island)

    ids = {f.id for f in pc.load_folders(island)}
    assert folder.id not in ids and keep.id in ids


def test_global_agents_folders_rename_and_delete_from_any_island(tmp_path: Path, monkeypatch, pushes) -> None:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "db")
    island = _island(tmp_path)
    _open_island(monkeypatch, island)
    api = PanelApiChatsMixin()
    crew = api.group_create("Crew", "", False, "_no_project")
    folder_id = next(f.id for f in pc.load_folders("") if f.group_hub_id == crew["id"])

    api.rename_folder(folder_id, "Crew 2")
    assert next(f.name for f in pc.load_folders("") if f.id == folder_id) == "Crew 2"

    api.delete_folder(folder_id, True)
    assert folder_id not in {f.id for f in pc.load_folders("")}
    assert pushes[-1]["removed_folder_ids"] == [folder_id]

    # A Global Agents chat takes its sub-agent chats with it, though another island is open.
    lead = pc.create_conversation(title="Lead", project_root="")
    sub = pc.create_conversation(title="Sub", project_root="")
    sub.parent_conv_id = lead.id
    pc.save_conversation(sub, "")
    api.delete_conversation(lead.id)
    assert pc.load_conversation(lead.id, "") is None and pc.load_conversation(sub.id, "") is None


def test_a_global_agents_group_goes_to_archive_and_back_whole(tmp_path: Path, monkeypatch, pushes) -> None:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "db")
    island = _island(tmp_path)
    _open_island(monkeypatch, island)
    api = PanelApiChatsMixin()
    crew = api.group_create("Crew", "", False, "_no_project")
    folder_id = next(f.id for f in pc.load_folders("") if f.group_hub_id == crew["id"])
    member = pc.create_conversation(title="Builder", folder_id=folder_id, project_root="")
    member.parent_conv_id = crew["id"]
    pc.save_conversation(member, "")

    api.delete_folder(folder_id, True)
    assert pc.load_conversation(member.id, "").folder_id == pc.ARCHIVE_FOLDER_ID

    api.move_conversation(crew["id"], "")
    back = next(f for f in pc.load_folders("") if f.group_hub_id == crew["id"])
    restored = pc.load_conversation(member.id, "")
    assert (restored.folder_id, restored.parent_conv_id) == (back.id, crew["id"])
