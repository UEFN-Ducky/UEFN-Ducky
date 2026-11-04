"""panel_chats.folders: sidebar rows and where a layout or chat lives."""

from __future__ import annotations

from types import SimpleNamespace

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import folders


def test_a_folder_row_names_its_project_only_when_given():
    f = SimpleNamespace(id="f", name="Team", parent_id="", sort_order=1.0, group_hub_id="hub")
    assert folders._folder_sidebar_row(f) == {"id": "f", "name": "Team", "parent_id": "", "sort_order": 1.0,
                                              "group_hub_id": "hub"}
    assert folders._folder_sidebar_row(f, project_slug="isle")["project_name"] == "isle"


def test_sidebar_context_tokens_falls_back_to_the_stored_size():
    assert folders._sidebar_context_tokens(SimpleNamespace(coding_agent_stats={"context_tokens": 7})) == 7
    assert folders._sidebar_context_tokens(SimpleNamespace()) == 0


def test_global_agents_layouts_save_with_no_island(monkeypatch):
    monkeypatch.setattr(_pa.PanelSettings, "load",
                        staticmethod(lambda: SimpleNamespace(uefn_project_root="C:/Isles/SomeIsland")))
    assert folders._layout_project_root("") is None
    assert folders._layout_project_root(folders.GLOBAL_SLUG) == ""


def test_rename_tells_every_window(monkeypatch):
    conv = SimpleNamespace(id="c", title="Old", folder_id="f")
    told: list[tuple] = []
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: conv)
    monkeypatch.setattr(_pa, "save_conversation", lambda c: None)
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: told.append((a, kw)))
    PanelApiChatsMixin().rename_conversation("c", "  New ")
    assert conv.title == "New" and told == [(("c", "New", "f"), {"open_tab": False})]
    PanelApiChatsMixin().rename_conversation("c", "   ")
    assert conv.title == "New"


def test_the_mixin_binds_the_moved_functions():
    assert PanelApiChatsMixin.list_folders is folders.list_folders
    assert PanelApiChatsMixin._layout_project_root is folders._layout_project_root
