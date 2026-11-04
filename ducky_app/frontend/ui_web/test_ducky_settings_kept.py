"""A turn that saves its older copy of a chat keeps the voice and style set meanwhile."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from frontend.settings import PanelSettings
from frontend.ui_web.chats import store as chat_store
from frontend.ui_web.chats.conversations import apply_ducky_config, create_conversation, load_conversation
from frontend.ui_web.chats.saves import save_conversation


@pytest.mark.parametrize("use_db", [True, False])
def test_running_turn_save_keeps_new_voice(monkeypatch, use_db):
    monkeypatch.setattr(chat_store, "_use_db", lambda: use_db)
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Lead", project_root=root)
        conv.messages = [{"role": "user", "text": "go", "content": "go", "ts": 1}]
        save_conversation(conv, root)
        turn_copy = load_conversation(conv.id, root)
        assert turn_copy is not None

        apply_ducky_config(
            conv.id,
            ducky_style="hacker",
            ducky_personality="calm",
            tts_voice="plugin:piper:en_US-ryan-high",
            project_root=root,
        )

        turn_copy.messages.append({"role": "assistant", "text": "done", "content": "done", "ts": 2})
        save_conversation(turn_copy, root)

        fresh = load_conversation(conv.id, root)
        assert fresh is not None
        assert fresh.tts_voice == "plugin:piper:en_US-ryan-high"
        assert fresh.ducky_style == "hacker"
        assert fresh.ducky_personality == "calm"
        assert fresh.messages[-1]["text"] == "done"
        assert turn_copy.tts_voice == "plugin:piper:en_US-ryan-high"


def test_a_later_edit_still_wins(monkeypatch):
    monkeypatch.setattr(chat_store, "_use_db", lambda: True)
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Lead", project_root=root)
        apply_ducky_config(conv.id, ducky_style="hacker", tts_voice="plugin:piper:a", project_root=root)
        apply_ducky_config(conv.id, ducky_style="hacker", tts_voice="plugin:piper:b", project_root=root)
        fresh = load_conversation(conv.id, root)
        assert fresh is not None and fresh.tts_voice == "plugin:piper:b"
