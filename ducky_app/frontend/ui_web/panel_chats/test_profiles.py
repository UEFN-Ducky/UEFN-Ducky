"""panel_chats.profiles: saving a profile renames the chats still named after it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


def test_a_renamed_profile_renames_its_chats_and_numbered_copies_only(monkeypatch):
    chats = {
        "a": SimpleNamespace(id="a", title="A", folder_id="", profile_id="p", ducky_name="Coder"),
        "b": SimpleNamespace(id="b", title="B", folder_id="", profile_id="p", ducky_name="Coder 2"),
        "c": SimpleNamespace(id="c", title="C", folder_id="", profile_id="p", ducky_name="Search lead"),
        "d": SimpleNamespace(id="d", title="D", folder_id="", profile_id="q", ducky_name="Coder"),
    }
    monkeypatch.setattr(_pa, "list_all_conversation_metadata", lambda: list(chats.values()))
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: chats.get(cid))
    monkeypatch.setattr(_pa, "save_conversation", lambda conv, **kw: None)
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: None)
    PanelApiChatsMixin()._sync_chats_for_profile({"id": "p", "name": "Verse Coder"}, "Coder")
    assert [chats[k].ducky_name for k in "abcd"] == ["Verse Coder", "Verse Coder 2", "Search lead", "Coder"]


def test_a_profile_that_is_not_an_object_is_refused():
    api = PanelApiChatsMixin()
    with pytest.raises(ValueError):
        api.save_agent_profile("nope")
    with pytest.raises(ValueError):
        api.save_agent_profile_override("id", "nope")


def test_a_new_profile_has_no_old_name():
    assert PanelApiChatsMixin._profile_name("") == ""
