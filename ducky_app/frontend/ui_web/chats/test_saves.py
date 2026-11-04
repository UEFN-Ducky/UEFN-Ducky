"""Chat saves: stub guards, the delta start, run rows, heals, and the old import path."""

from __future__ import annotations

import pytest

from frontend.settings import PanelSettings
from frontend.ui_web.chats import saves
from frontend.ui_web.chats.conversations import create_conversation, load_conversation


@pytest.fixture(autouse=True)
def _flushed():
    yield
    saves.flush_chat_saves()


def test_a_stub_or_a_copy_without_the_assistant_keeps_the_stored_rows() -> None:
    asst = {"role": "assistant", "content": "a"}
    user = {"role": "user", "content": "q"}
    assert saves._keeps_stored_rows([], [user]) is True
    assert saves._keeps_stored_rows([user], [user, asst]) is True
    assert saves._keeps_stored_rows([user, asst], [user, asst]) is False
    assert saves._disk_has_assistant_incoming_does_not([user], [user, asst]) is True
    assert saves._disk_has_assistant_incoming_does_not([user, asst], [user]) is False


def test_the_delta_starts_at_the_last_user_message_or_lower() -> None:
    msgs = [{"role": "user"}, {"role": "assistant"}, {"role": "user"}, {"role": "assistant"}]
    assert saves._delta_start(msgs, None) == 2
    assert saves._delta_start(msgs, 1) == 1
    assert saves._delta_start(msgs, 3) == 2
    assert saves._delta_start([], None) == 0


def test_a_run_row_is_found_after_the_last_user_message_only() -> None:
    msgs = [
        {"role": "assistant", "run_id": "r"},
        {"role": "user"},
        {"role": "assistant", "run_id": "r"},
    ]
    assert saves._run_row_index(msgs, "r") == 2
    assert saves._run_row_index(msgs[:2], "r") == -1
    assert saves._run_row_index(msgs, "") == -1


def test_a_killed_turn_gets_one_interrupted_reply(tmp_path) -> None:
    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), "", title="Turn", project_root=root)
    conv.messages = [{"role": "user", "content": "go", "ts": 1}]
    saves.save_conversation(conv, root, wait=True)

    assert saves.heal_killed_coding_turn(conv.id, "run-1", project_root=root) is True
    saves.flush_chat_saves()
    fresh = load_conversation(conv.id, root)
    assert fresh.messages[-1]["error"] == saves.KILLED_TURN_ERROR
    assert fresh.messages[-1]["run_id"] == "run-1"
    assert saves.heal_killed_coding_turn(conv.id, "run-1", project_root=root) is False


def test_a_patch_on_the_old_path_reaches_callers_inside_the_package(monkeypatch, tmp_path) -> None:
    from frontend.ui_web import project_chats

    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), "", title="Patched", project_root=root)
    rows: list[str] = []
    real = saves._save_conversation_rows
    monkeypatch.setattr(project_chats, "_save_conversation_rows", lambda c, *a: rows.append(c.id) or real(c, *a))
    assert saves._save_conversation_rows is not real
    conv.title = "Patched again"
    saves.save_conversation(conv, root, wait=True)
    assert rows == [conv.id]
    monkeypatch.undo()
    assert saves._save_conversation_rows is real
    assert project_chats.save_conversation is saves.save_conversation
    assert project_chats._WRITER is saves._WRITER
