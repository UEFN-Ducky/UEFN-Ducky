"""A send while a turn runs never stops it: the text is queued and offered as a steer.

Only the Stop button (cancel_agent, by_user=True) ends a turn; the follow-up typed after
it runs once that turn is idle.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from backend.agent import a2a_broker
from frontend.ui_web import steer
from frontend.ui_web.agent_turns import routing, state
from frontend.ui_web.agent_turns.testing import turns_patched


def test_run_message_queues_follow_up_instead_of_cancelling_live_run():
    pushes: list[dict] = []
    conv = SimpleNamespace(id="chat-1", title="t", messages=[], model="gpt-test", provider="openai",
                           coding_agent="ducky", folder_id=None, file_path="")

    with (
        turns_patched("_in_bridge_process", return_value=False),
        turns_patched("is_agent_running", return_value=True),
        turns_patched("cancel_agent") as cancel,
        turns_patched("wait_for_idle") as wait,
        turns_patched("load_conversation", return_value=conv) as load,
        turns_patched("_pending_agent_messages", {}),
        turns_patched("_pending_steered", {}),
        turns_patched("_deliver_when_idle") as deliver,  # its idle wait runs on its own thread
        patch.object(a2a_broker, "_retry_queue_later", lambda cid: None),
    ):
        try:
            out = routing.run_message("chat-1", "fix spacing", "agent", "gpt-test", push=pushes.append)
            assert out == "queued"
            assert state._pending_agent_messages["chat-1"] == ["fix spacing"]
            assert [m["text"] for m in steer._inbox.get("chat-1", [])] == ["fix spacing"]
        finally:
            steer.end_run("chat-1")

    assert not cancel.called and not wait.called
    deliver.assert_called_once_with("chat-1")  # the parked send runs once the turn is idle
    assert not load.called  # no second turn started over the live one
    assert not any("already running" in str(e.get("text")) for e in pushes)
