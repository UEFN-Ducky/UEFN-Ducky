"""agent_turns.pending: sends that wait (no model yet, or a busy chat) and their delivery."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.ui_web.agent_turns import pending, state


@pytest.fixture
def empty(monkeypatch):
    from backend.agent import a2a_broker

    for name in ("_pending_agent_messages", "_pending_person_sends", "_pending_steered"):
        box: dict = {}
        monkeypatch.setattr(state, name, box)
        monkeypatch.setattr(pending, name, box)
    monkeypatch.setattr(pending, "_held_messages", {})
    monkeypatch.setattr(a2a_broker, "_retry_queue_later", lambda cid: None)


def test_a_chat_with_no_model_keeps_each_message_once(empty, monkeypatch):
    from frontend import notifications

    monkeypatch.setattr(notifications, "alert", lambda *a, **kw: None)
    seen: list[dict] = []
    conv = SimpleNamespace(id="c", title="Chat")
    for _ in range(2):
        assert pending._hold_for_model(conv, "hi", "agent", None, "", None, "pick one", seen.append) \
            == pending.HELD_FOR_MODEL
    assert pending.held_for_model("c") == 1
    assert seen[-1] == {"type": "model_needed", "conv_id": "c", "held": 1, "text": "pick one"}


def test_queued_agent_messages_go_to_the_next_delivery(empty):
    pending._queue_pending("c", "one")
    pending._queue_pending("c", "two")
    assert pending.take_pending_agent_messages("c") == ["one", "two"]
    assert pending.take_pending_agent_messages("c") == []


def test_a_send_with_files_into_a_busy_chat_is_its_own_next_turn(empty):
    files = [{"kind": "image", "name": "a.png"}]
    assert pending._queue_person_send("c", "look", "plan", {"attachments": files}) == "queued"
    assert state._pending_person_sends["c"] == [{"text": "look", "mode": "plan", "attachments": files}]
    assert pending._queue_person_send("c", "  ", "agent", {}) == ""


def test_the_broker_tap_handles_each_runs_stop_once(empty, monkeypatch):
    from backend.agent import a2a_broker
    from frontend.ui_web import admission, steer

    stops: list[str] = []
    monkeypatch.setattr(admission, "release", lambda cid: None)
    monkeypatch.setattr(steer, "end_run", lambda cid: None)
    monkeypatch.setattr(a2a_broker, "on_agent_stopped", lambda cid, reason, **kw: stops.append(reason))
    monkeypatch.setattr(a2a_broker, "stats", lambda: {})
    monkeypatch.setattr(a2a_broker, "automatic_work_blocked", lambda cid, queued=False: True)
    seen: list[dict] = []
    tap = pending._make_broker_tap(seen.append, "c")
    stop = {"type": "agent_stopped", "conv_id": "c", "run_id": "r1", "reason": "done"}
    tap(stop)
    tap(stop)
    assert stops == ["done"] and seen == [stop, stop]
