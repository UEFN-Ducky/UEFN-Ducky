"""panel_chats.conversations: one chat's row, config, mute flag and attached files."""

from __future__ import annotations

from types import SimpleNamespace

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import conversations


def test_a_chat_row_defaults_its_optional_fields():
    conv = SimpleNamespace(id="c", title="T", ducky_style="s", ducky_name=None, ducky_personality="p",
                           file_path="", model=None, provider=None)
    row = conversations._conversation_row(conv)
    assert row["coding_agent"] == "ducky" and row["model"] == "" and row["tts_speed"] == 0.0


def test_mute_stores_the_flag_and_reloads_the_sidebar(monkeypatch):
    conv = SimpleNamespace(id="c", title="T", folder_id="f", muted=False)
    told: list[tuple] = []
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: conv if cid == "c" else None)
    monkeypatch.setattr(_pa, "save_conversation", lambda c: None)
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: told.append(a))
    api = PanelApiChatsMixin()
    assert api.set_muted(" c ", True) == {"ok": True, "id": "c", "muted": True, "is_group": False}
    assert told == [("c", "T", "f")]
    assert api.set_muted("x", True)["ok"] is False


def test_a_config_that_is_not_an_object_is_refused():
    assert PanelApiChatsMixin().apply_ducky_config("c", "nope") == {"ok": False, "error": "config must be an object"}


def test_an_unreadable_chat_file_is_refused():
    assert PanelApiChatsMixin().save_chat_file("c", "a.txt", "not base64!") == {
        "ok": False, "error": "The file could not be read."}


def test_bad_template_files_json_is_refused():
    import pytest

    with pytest.raises(ValueError, match="invalid files_json"):
        PanelApiChatsMixin().save_custom_verse_template("n", "i", files_json="{nope")
