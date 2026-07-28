"""Clearing chat messages must drop poisoned coding-agent resume sessions."""

from __future__ import annotations

from frontend.chat_store import Conversation
from frontend.ui_web.context_control import clear_conversation_messages


def test_clear_conversation_messages_drops_upstream_session(tmp_path):
    conv = Conversation(
        id="c1",
        title="broken",
        messages=[{"role": "user", "text": "hi"}],
        upstream_session_id="claude_code:sess-poison",
        coding_agent_stats={"model": "x", "context_tokens": 99999},
    )
    clear_conversation_messages(conv, project_root=str(tmp_path))
    assert conv.messages == []
    assert conv.upstream_session_id == ""
    assert conv.coding_agent_stats is None
