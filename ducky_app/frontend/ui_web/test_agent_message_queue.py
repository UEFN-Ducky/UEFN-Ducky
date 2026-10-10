"""Agent-to-agent messages never cancel the chat they reach: a busy chat holds them."""

from __future__ import annotations

import time
from types import SimpleNamespace

from frontend.ui_web import agent_modes as am


def test_a_busy_chat_holds_agent_messages_and_runs_them_after_its_turn(monkeypatch) -> None:
    cancels: list[object] = []
    monkeypatch.setattr(am, "load_conversation", lambda cid, **kw: SimpleNamespace(id=cid))
    monkeypatch.setattr(am, "is_agent_running", lambda cid: True)
    monkeypatch.setattr(am, "cancel_agent", lambda cid=None: cancels.append(cid))
    monkeypatch.setattr(am, "_pending_agent_messages", {})

    assert am.run_message("c1", "report 1", "agent", "", queue_if_busy=True, _local=True) == "queued"
    assert am.run_message("c1", "report 2", "agent", "", queue_if_busy=True, _local=True) == "queued"
    assert cancels == []  # the coordinator's turn keeps running
    assert am._pending_agent_messages["c1"] == ["report 1", "report 2"]

    started: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(am, "wait_for_idle", lambda cid, timeout=None: True)
    monkeypatch.setattr(am, "run_message", lambda cid, text, mode, model, **kw: started.append((cid, text, kw)) or "run")
    am._deliver_pending_agent_messages("c1")
    deadline = time.time() + 5
    while not started and time.time() < deadline:
        time.sleep(0.02)
    assert started and started[0][:2] == ("c1", "report 1\n\nreport 2")
    assert started[0][2].get("queue_if_busy") is True
    assert "c1" not in am._pending_agent_messages


def test_a_person_pressing_stop_drops_held_agent_messages(monkeypatch) -> None:
    monkeypatch.setattr(am, "_pending_agent_messages", {"c2": ["held"]})
    am.cancel_agent("c2")
    assert "c2" not in am._pending_agent_messages


def test_terminal_quota_tap_retains_pending_until_cooldown(monkeypatch):
    from backend.agent import a2a_broker as broker
    monkeypatch.setattr(broker, "_cooldowns", {}, raising=False)
    monkeypatch.setattr(broker, "_stopped", set(), raising=False)
    monkeypatch.setattr(broker, "_uncertain_chats", set(), raising=False)
    monkeypatch.setattr(broker, "_retry_queue_later", lambda cid: None)
    monkeypatch.setattr(am, "_pending_agent_messages", {"limited": ["queued work"]})
    calls = []
    monkeypatch.setattr(am, "_deliver_pending_agent_messages", lambda cid: calls.append(cid))
    tap = am._make_broker_tap(lambda event: None, "limited")
    event = {"type": "agent_stopped", "conv_id": "limited", "reason": "error",
             "detail": '{"error":{"code":"insufficient_quota"}}'}
    tap(event)
    tap(event)
    assert calls == []
    assert am._pending_agent_messages == {"limited": ["queued work"]}
