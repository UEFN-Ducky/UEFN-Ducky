"""Roster write lanes on save (206e154d10): an older copy never drops a lane set since."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.settings import PanelSettings
from frontend.ui_web.chats.conversations import create_conversation, load_conversation, load_conversation_meta
from frontend.ui_web.chats.lanes import _keep_stored_lanes
from frontend.ui_web.chats.saves import flush_chat_saves, save_conversation


@pytest.fixture(autouse=True)
def _flushed():
    yield
    flush_chat_saves()


def _member(mid: str, lane=None, at: float = 0.0) -> dict:
    return {"member_conv_id": mid, "write_allowed": lane, "lane_set_by": "lead" if at else "", "lane_set_at": at}


def _hub(root: str, members: list[dict]):
    conv = create_conversation(PanelSettings.load(), "", title="group", project_root=root)
    hub = load_conversation(conv.id, root)
    hub.is_group = True
    hub.group_members = members
    save_conversation(hub, root, wait=True)
    return load_conversation_meta(hub.id, root)


def _lanes(hub_id: str, root: str) -> dict:
    stored = load_conversation_meta(hub_id, root)
    return {r["member_conv_id"]: r["write_allowed"] for r in stored.group_members}


def test_a_plain_chat_or_a_missing_stored_copy_is_left_alone() -> None:
    chat = SimpleNamespace(is_group=False, group_members=[_member("A")])
    _keep_stored_lanes(chat, {"group_members": [_member("A", ["a/**"], 10.0)]})
    assert chat.group_members == [_member("A")]
    hub = SimpleNamespace(is_group=True, group_members=[_member("A")])
    _keep_stored_lanes(hub, None)
    assert hub.group_members == [_member("A")]


def test_a_hub_saved_twice_with_different_lanes_keeps_both(tmp_path) -> None:
    root = str(tmp_path)
    hub = _hub(root, [_member("A"), _member("B")])
    first, second = load_conversation_meta(hub.id, root), load_conversation_meta(hub.id, root)
    first.group_members = [_member("A", ["a/**"], 10.0), _member("B")]
    save_conversation(first, root, wait=True)
    second.group_members = [_member("A"), _member("B", ["b/**"], 11.0)]
    save_conversation(second, root, wait=True)
    assert _lanes(hub.id, root) == {"A": ["a/**"], "B": ["b/**"]}


def test_an_empty_lane_survives_an_older_copy_a_save_and_a_reload(tmp_path) -> None:
    root = str(tmp_path)
    hub = _hub(root, [_member("A"), _member("B")])
    stale = load_conversation_meta(hub.id, root)
    fresh = load_conversation_meta(hub.id, root)
    fresh.group_members = [_member("A"), _member("B", [], 10.0)]
    save_conversation(fresh, root, wait=True)
    stale.group_members = stale.group_members + [_member("C")]  # invite C from the old copy
    save_conversation(stale, root, wait=True)
    flush_chat_saves()
    assert _lanes(hub.id, root) == {"A": None, "B": [], "C": None}


def test_a_merged_writer_batch_keeps_the_newest_roster(tmp_path) -> None:
    root = str(tmp_path)
    hub = _hub(root, [_member("A"), _member("B")])
    stale = load_conversation_meta(hub.id, root)
    fresh = load_conversation_meta(hub.id, root)
    fresh.group_members = [_member("A", ["a/**"], 10.0), _member("B")]
    save_conversation(fresh, root)  # no placement change: both saves are queued
    stale.group_members = stale.group_members + [_member("C")]
    save_conversation(stale, root)
    assert _lanes(hub.id, root) == {"A": ["a/**"], "B": None, "C": None}  # the queued copy
    flush_chat_saves()
    assert _lanes(hub.id, root) == {"A": ["a/**"], "B": None, "C": None}
