"""After a checkpoint lands, its chat object learns the new rev and row hashes without a
row being read back and decoded."""

from __future__ import annotations

import pytest

from backend.store.repos import chats as repo
from frontend.settings import PanelSettings
from frontend.ui_web.chats import saves as chat_saves
from frontend.ui_web.chats.conversations import create_conversation, load_conversation
from frontend.ui_web.chats.saves import flush_chat_saves, save_conversation


@pytest.fixture(autouse=True)
def _flushed():
    yield
    flush_chat_saves()


def _turn(root: str):
    conv = create_conversation(PanelSettings.load(), "", title="turn", project_root=root)
    conv = load_conversation(conv.id, root)
    conv.messages = [{"role": "user", "content": "go", "ts": 1}, {"role": "assistant", "content": "ok", "ts": 2},
                     {"role": "user", "content": "again", "ts": 3}]
    save_conversation(conv, root, wait=True)
    return conv


def test_message_hashes_since_matches_messages_since(tmp_path) -> None:
    conv = _turn(str(tmp_path))
    _, rev, count, hashes = repo.messages_since(conv.id, 1, with_hashes=True)
    assert repo.message_hashes_since(conv.id, 1) == (rev, count, hashes)
    assert repo.message_hashes_since("nobody", 0) == (None, 0, [])


def test_the_checkpoint_follow_up_decodes_no_row(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _turn(root)

    armed = [True]

    def guard(real):
        def wrapped(*a, **kw):
            assert not armed[0], "a row was decoded after the checkpoint"
            return real(*a, **kw)
        return wrapped

    # Not monkeypatch.undo(): that would also undo the test database's own patches.
    monkeypatch.setattr(repo, "_messages", guard(repo._messages))
    monkeypatch.setattr(repo, "_message", guard(repo._message))
    for step in ("working", "still working"):
        chat_saves.upsert_in_flight_assistant(conv, {"role": "assistant", "content": step}, run_id="r1", project_root=root)
        flush_chat_saves()
    armed[0] = False

    assert not getattr(conv, "_stale_tail", False)
    _, rev, count, hashes = repo.messages_since(conv.id, 0, with_hashes=True)
    assert (conv._msg_rev, len(conv.messages)) == (rev, count) == (rev, 4)
    assert conv._base_hashes == hashes
    assert repo.messages_get(conv.id)[-1]["content"] == "still working"
