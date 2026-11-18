"""run_message_and_wait reads the reply from the row store's tail, never by loading the chat."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.ui_web.agent_turns import routing
from frontend.ui_web.agent_turns.run import _last_assistant_text

ENDS_IN_TOOL = [
    {"role": "user", "content": "go", "ts": 1.0},
    {"role": "assistant", "content": "Looking now.", "ts": 2.0},
    {"role": "assistant", "content": "", "ts": 3.0,
     "tool_calls": [{"id": "t1", "name": "workspace_read_file", "arguments": {}}]},
    {"role": "tool", "content": "file text", "tool_call_id": "t1", "ts": 4.0},
]
ENDS_IN_TEXT = [
    {"role": "user", "content": "go", "ts": 1.0},
    {"role": "assistant", "content": "first", "ts": 2.0},
    {"role": "assistant", "content": "  Done: pushed abc1234.  ", "ts": 3.0},
]

EVENTS = {
    "assistant_done": lambda cid: {"type": "assistant_done", "conv_id": cid},
    "agent_stopped": lambda cid: {"type": "agent_stopped", "conv_id": cid, "reason": "done"},
    "linked_agent": lambda cid: {"type": "linked_agent", "child_conv_id": cid, "status": "done"},
}


@pytest.mark.parametrize("messages", [ENDS_IN_TOOL, ENDS_IN_TEXT], ids=["tool", "text"])
@pytest.mark.parametrize("event", sorted(EVENTS))
def test_the_last_reply_is_read_without_a_chat_load(monkeypatch, messages, event) -> None:
    from backend.store import switch
    from backend.store.repos import chats as repo

    cid = f"route-{event}-{len(messages)}"
    monkeypatch.setattr(switch, "use_db", lambda table: True)
    repo.conv_save("proj_route", {"id": cid, "title": "c", "created": 1.0, "updated": 1.0}, messages=messages)
    monkeypatch.setattr(routing, "load_conversation", lambda *a, **k: pytest.fail("loaded the whole chat"))
    monkeypatch.setattr(routing, "get_active_conv_id", lambda: "")

    def fake_run(conv_id, *a, push=None, **k):
        push(EVENTS[event](conv_id))
        return "run1"

    monkeypatch.setattr(routing, "run_message", fake_run)
    monkeypatch.setattr(routing, "_resolve_push", lambda push: (lambda e: None))
    result = routing.run_message_and_wait(cid, "hi", _local=True, timeout_sec=5)
    assert result["status"] == "done"
    assert result["assistant_text"] == _last_assistant_text(SimpleNamespace(messages=messages))
