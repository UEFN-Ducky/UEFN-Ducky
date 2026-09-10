"""A sidebar stub (messages stripped) must not overwrite chat history on disk."""

from __future__ import annotations

import tempfile
from pathlib import Path

from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import create_conversation, load_conversation, save_conversation


def test_save_conversation_does_not_blank_existing_messages():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Keep me", project_root=root)
        conv.messages = [{"role": "user", "text": "hello", "content": "hello", "ts": 1}]
        save_conversation(conv, root)

        stub = load_conversation(conv.id, project_root=root)
        assert stub is not None
        stub.messages = []
        save_conversation(stub, root, touch_updated=False)

        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        assert len(fresh.messages) == 1
        assert fresh.messages[0]["text"] == "hello"


def test_save_conversation_does_not_drop_assistant_for_user_only_stub():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Keep tools", project_root=root)
        conv.messages = [
            {"role": "user", "text": "do it", "content": "do it", "ts": 1},
            {
                "role": "assistant",
                "content": "",
                "run_id": "r1",
                "incomplete": True,
                "blocks": [{"type": "tool_call", "name": "Read", "status": "success"}],
            },
        ]
        save_conversation(conv, root)

        stub = load_conversation(conv.id, project_root=root)
        assert stub is not None
        stub.messages = [{"role": "user", "text": "do it", "content": "do it", "ts": 1}]
        save_conversation(stub, root, touch_updated=False)

        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        assert any(m.get("role") == "assistant" for m in fresh.messages)
        assert fresh.messages[-1]["blocks"][0]["name"] == "Read"
