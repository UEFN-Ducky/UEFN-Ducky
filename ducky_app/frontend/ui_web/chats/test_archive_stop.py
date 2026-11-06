"""Archiving a chat stops it: its turn is stopped and its terminals close."""

from __future__ import annotations

from frontend.ui_web.chats import archive_stop


def test_archived_chats_are_stopped_and_their_terminals_closed() -> None:
    cancelled: list[str] = []
    closed: list[list[str]] = []
    archive_stop.stop_archived(["seat", "", "lead", "seat"], cancel=cancelled.append,
                               close_terminals=lambda ids: closed.append(ids) or ids, wait=True)
    assert cancelled == ["lead", "seat"]
    assert closed == [["lead", "seat"]]


def test_a_failing_stop_never_breaks_the_archive() -> None:
    def boom(_conv_id: str) -> None:
        raise RuntimeError("no panel")

    closed: list[list[str]] = []
    archive_stop.stop_archived(["seat"], cancel=boom, close_terminals=lambda ids: closed.append(ids) or ids, wait=True)
    assert closed == [["seat"]]
    archive_stop.stop_archived([], cancel=boom, close_terminals=boom, wait=True)  # nothing to do


def test_moving_a_chat_to_archive_stops_it_and_its_sub_agents(monkeypatch, tmp_path) -> None:
    from frontend.archive_folder import ARCHIVE_FOLDER_ID
    from frontend.ui_web.chats import conversations

    stopped: list[list[str]] = []
    monkeypatch.setattr(archive_stop, "stop_archived", lambda ids, **kw: stopped.append(sorted(ids)))
    root = str(tmp_path)
    conv = conversations.create_conversation(title="Soak seat", project_root=root)
    conversations.move_conversation(conv.id, ARCHIVE_FOLDER_ID, root)
    assert stopped == [[conv.id]]
    conversations.move_conversation(conv.id, "", root)  # back to active: nothing stops
    assert stopped == [[conv.id]]


def test_deleting_a_chat_stops_it_and_its_sub_agents(monkeypatch, tmp_path) -> None:
    from frontend.ui_web.chats import conversations

    stopped: list[list[str]] = []
    monkeypatch.setattr(archive_stop, "stop_archived", lambda ids, **kw: stopped.append(sorted(ids)))
    root = str(tmp_path)
    lead = conversations.create_conversation(title="Lead", project_root=root)
    sub = conversations.create_conversation(title="Sub", parent_conv_id=lead.id, project_root=root)
    conversations.delete_conversation(lead.id, root)
    assert stopped == [sorted([lead.id, sub.id])]
    assert conversations.load_conversation(lead.id, root) is None


def test_a_stuck_stop_is_waited_for_only_up_to_the_timeout() -> None:
    import threading
    import time

    release = threading.Event()
    t0 = time.monotonic()
    archive_stop.stop_archived(["seat"], cancel=lambda _c: release.wait(5), close_terminals=lambda ids: ids,
                               wait=True, timeout=0.2)
    took = time.monotonic() - t0
    release.set()
    assert 0.15 <= took < 2.0


def _files_chat(monkeypatch, tmp_path, title: str = "Doomed"):
    from frontend.ui_web.chats import conversations

    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    root = str(tmp_path / "Island")
    (tmp_path / "Island" / "Content").mkdir(parents=True)
    conv = conversations.create_conversation(title=title, project_root=root)
    meta = conversations._find_conversation_meta(conv.id, root)
    assert meta is not None and meta.is_file()
    return conversations, root, conv, meta


def test_the_stop_finishes_before_the_chat_files_go(monkeypatch, tmp_path) -> None:
    # Nov 6 2026: the stop ran on its own thread and read conversation.json while rmtree ran;
    # Windows refused the unlink, the delete swallowed it, and the chat came back.
    import threading

    conversations, root, conv, meta = _files_chat(monkeypatch, tmp_path)
    seen: list[tuple[bool, bool]] = []

    def reading_cancel(conv_id: str) -> None:
        with open(meta, encoding="utf-8"):  # holds the file the way the real stop's read does
            threading.Event().wait(0.3)
        seen.append((conv_id == conv.id, meta.is_file()))

    monkeypatch.setattr(archive_stop, "_cancel", reading_cancel)
    monkeypatch.setattr(archive_stop, "_close_terminals", lambda ids: [])
    conversations.delete_conversation(conv.id, root)
    assert seen == [(True, True)]  # stopped while the files were still there
    assert conversations.load_conversation(conv.id, root) is None
    assert not meta.parent.exists()


def test_a_reader_that_lets_go_soon_never_keeps_the_chat(monkeypatch, tmp_path) -> None:
    import threading

    conversations, root, conv, meta = _files_chat(monkeypatch, tmp_path)
    monkeypatch.setattr(archive_stop, "stop_archived", lambda ids, **kw: None)
    held = open(meta, encoding="utf-8")
    threading.Timer(0.2, held.close).start()
    try:
        conversations.delete_conversation(conv.id, root)
    finally:
        held.close()
    assert conversations.load_conversation(conv.id, root) is None


def test_a_delete_that_cannot_remove_the_chat_says_so(monkeypatch, tmp_path) -> None:
    import shutil

    import pytest

    conversations, root, conv, meta = _files_chat(monkeypatch, tmp_path)
    monkeypatch.setattr(archive_stop, "stop_archived", lambda ids, **kw: None)
    monkeypatch.setattr(conversations, "_RMTREE_TRIES", 2)
    tries: list[str] = []

    def refused(path, *a, **k):
        tries.append(str(path))
        raise PermissionError(13, "in use", str(meta))

    monkeypatch.setattr(shutil, "rmtree", refused)
    with pytest.raises(OSError, match="Couldn't delete the chat"):
        conversations.delete_conversation(conv.id, root)
    assert len(tries) == 2  # retried before giving up
    assert conversations.load_conversation(conv.id, root) is not None  # reported, not hidden


def test_a_stale_layout_after_a_real_delete_never_brings_the_chat_back(monkeypatch, tmp_path) -> None:
    # The product path of test_sidebar_layout_stale's deleted-chat case, with the real
    # stop_archived (that file runs the stop inline for its layout tests).
    from frontend.ui_web.chats import folders as chat_folders

    conversations, root, conv, _meta = _files_chat(monkeypatch, tmp_path, "Gone")
    folder = chat_folders.create_folder("Box", project_root=root)
    stale = conversations.load_conversation(conv.id, root)
    conversations.list_conversations(project_root=root)
    conversations.delete_conversation(conv.id, root)
    assert conversations.load_conversation(conv.id, root) is None

    real_load = conversations._load_all_conversations
    monkeypatch.setattr(conversations, "_load_all_conversations", lambda *a, **k: [*real_load(*a, **k), stale])
    chat_folders.apply_sidebar_layout(
        folders=[], chats=[{"id": conv.id, "folder_id": folder.id, "sort_order": 3}], project_root=root
    )
    assert conversations.load_conversation(conv.id, root) is None


def test_a_running_agent_whose_chat_is_gone_can_still_be_stopped(monkeypatch) -> None:
    import json

    from backend.agent import a2a_client
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web.agent_turns.testing import patch_turns

    cancelled: list[str] = []
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(panel, "load_conversation", lambda cid, project_root=None: None)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: cid == "gone-but-running")
    patch_turns(monkeypatch, "cancel_agent", cancelled.append)
    monkeypatch.setattr(a2a_client, "on_agent_cancelled_by_user", lambda cid: None)
    assert json.loads(panel.ducky_agent_stop("gone-but-running"))["stopped"] == ["gone-but-running"]
    assert cancelled == ["gone-but-running"]
    import pytest

    with pytest.raises(ValueError):
        panel.ducky_agent_stop("never-existed")


def test_archiving_a_group_stops_its_members(monkeypatch, tmp_path) -> None:
    from frontend.archive_folder import ARCHIVE_FOLDER_ID
    from frontend.settings import PanelSettings
    from frontend.ui_web.chats import conversations
    from frontend.ui_web.test_group_folder_hubs import _make_group

    stopped: list[list[str]] = []
    monkeypatch.setattr(archive_stop, "stop_archived", lambda ids, **kw: stopped.append(sorted(ids)))
    root = str(tmp_path)
    settings = PanelSettings.load()
    folder, hub = _make_group(settings, "Squad", "", root)
    member = conversations.create_conversation(settings, folder.id, title="Soak seat",
                                               parent_conv_id=hub.id, project_root=root)
    conversations.move_conversation(hub.id, ARCHIVE_FOLDER_ID, root)
    assert stopped and member.id in stopped[0] and hub.id in stopped[0]
