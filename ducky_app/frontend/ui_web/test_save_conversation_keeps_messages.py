"""A sidebar stub (messages stripped) must not overwrite chat history on disk."""

from __future__ import annotations

import tempfile
from pathlib import Path

from frontend.settings import PanelSettings
from frontend.ui_web.chats.conversations import create_conversation, load_conversation
from frontend.ui_web.chats.saves import save_conversation


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


def test_a_running_turn_keeps_a_move_and_rename_made_while_it_works():
    from frontend.ui_web.chats.conversations import move_conversation, rename_conversation
    from frontend.ui_web.chats.folders import create_folder
    from frontend.ui_web.chats.saves import append_message, upsert_in_flight_assistant

    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Verse Coder 6", project_root=root)
        runner = load_conversation(conv.id, project_root=root)  # the copy a turn holds
        assert runner is not None
        coders = create_folder("Coders", "", root)
        move_conversation(conv.id, coders.id, root)  # dragged into a group while it works
        rename_conversation(conv.id, "Verse Coder 6b", root)

        append_message(runner, {"role": "user", "text": "go", "content": "go", "ts": 1}, root)
        upsert_in_flight_assistant(runner, {"role": "assistant", "content": "on it"}, run_id="r1", project_root=root)
        runner.token_usage = {"total_input": 5}  # a mid-turn usage save
        save_conversation(runner, root)

        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        assert (fresh.folder_id, fresh.title) == (coders.id, "Verse Coder 6b")
        assert [m["role"] for m in fresh.messages] == ["user", "assistant"]

        # The turn's own change still lands.
        runner.folder_id = ""
        save_conversation(runner, root)
        assert load_conversation(conv.id, project_root=root).folder_id == ""


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
