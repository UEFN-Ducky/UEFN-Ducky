"""A chat load never waits behind a busy chat writer (a team run's checkpoints)."""

from __future__ import annotations

import time
from pathlib import Path

import frontend.ui_web.panel_api  # noqa: F401 - mixins import the package root first
from frontend.settings import PanelSettings
from frontend.ui_web.chats import conversations as chat_convs
from frontend.ui_web.chats import saves as chat_saves
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


class _BusyWriter:
    """A writer stuck on other chats: this chat's checkpoint waits, a flush sleeps its timeout."""

    def checkpoint_waits(self, conv_id: str) -> bool:
        return True

    def peek(self, conv_id: str):
        return None

    def flush(self, conv_id=None, timeout: float = 30.0) -> bool:
        time.sleep(timeout)
        return False


def test_short_chat_loads_under_a_second_while_the_writer_is_busy(tmp_path: Path, monkeypatch) -> None:
    project = str(tmp_path / "Island")
    Path(project).mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = project
    s.save()
    conv = chat_convs.create_conversation(title="Short", project_root=project, skill_snapshot="x")
    conv = chat_convs.load_conversation(conv.id, project)
    conv.messages = [{"role": "user", "content": "hi", "text": "hi", "ts": 1},
                     {"role": "assistant", "content": "Hello.", "text": "Hello.", "ts": 2}]
    chat_saves.save_conversation(conv, project, wait=True)
    api = PanelApiChatsMixin()
    whole = api.load_messages(conv.id)

    monkeypatch.setattr(chat_saves, "_WRITER", _BusyWriter())
    for args in ((conv.id, None, 80), (conv.id,)):
        started = time.monotonic()
        rows = api.load_messages(*args)
        took = time.monotonic() - started
        assert took < 1.0, (args, took)  # was 30 s: load_conversation's writer flush
        assert rows == whole
