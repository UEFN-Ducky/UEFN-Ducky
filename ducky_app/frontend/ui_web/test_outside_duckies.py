"""The Duckies list is one list, whatever project is selected."""

from __future__ import annotations

import types

from frontend.ui_web import panel_api as pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


def _conv(cid: str):
    return types.SimpleNamespace(
        id=cid,
        title=cid,
        sort_order=0,
        updated=0,
        ducky_style="",
        ducky_name="",
        profile_id="",
        ducky_personality="",
        tts_voice="",
        tts_speed=0,
        file_path="",
        model="",
        provider="",
        coding_agent="ducky",
        thinking_effort="",
        terminal_session_id="",
        folder_id="",
        parent_conv_id="",
        is_group=False,
        leader_conv_id="",
        group_members=[],
        tool_call_count=0,
        file_count=0,
    )


def test_the_list_is_the_one_list_on_any_project(monkeypatch):
    roots: list[str | None] = []
    merged: list[bool] = []

    def fake_list(root=None):
        roots.append(root)
        return [_conv("helper")]

    monkeypatch.setattr(pa, "list_all_conversation_metadata", fake_list)
    monkeypatch.setattr("frontend.ui_web.chats.folders.merge_into_one_list", lambda: merged.append(True) or 0)
    monkeypatch.setattr(pa.PanelSettings, "load", staticmethod(lambda: types.SimpleNamespace(uefn_project_root="C:/island")))
    monkeypatch.setattr(pa.PanelApi, "_sidebar_context_tokens", staticmethod(lambda _c: 0))

    rows = PanelApiChatsMixin().list_all_conversations(True)
    assert [row["id"] for row in rows] == ["helper"]
    assert "project_slug" not in rows[0]
    # Older per-project rows move into the list first; one read, no project named.
    assert merged == [True] and roots == [None]


def test_global_agents_create_has_no_island(monkeypatch):
    from frontend.ui_web.chats import store as chat_store

    monkeypatch.setattr(chat_store, "project_root_for_slug", lambda slug: None)
    island = r"C:\island"
    assert chat_store.create_project_root("_no_project", island) == ""
    assert chat_store.create_project_root(chat_store.project_slug(island), island) is None
    try:
        chat_store.create_project_root("missing", island)
    except ValueError as exc:
        assert "Unknown project" in str(exc)
    else:
        raise AssertionError("missing project should fail")
