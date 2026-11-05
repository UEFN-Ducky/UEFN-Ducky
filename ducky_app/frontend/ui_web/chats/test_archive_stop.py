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
