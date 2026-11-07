"""Saved questions end when their chat is stopped, moved past or deleted; a waiting one survives."""

from __future__ import annotations

import time

from backend.store.repos import chats
from frontend.ui_web import ui_rpc

PROJECT = "proj_stale_questions"


def _restart(monkeypatch):
    with ui_rpc._lock:
        ui_rpc._pending.clear()
    monkeypatch.setattr(ui_rpc, "_restored_store", "")


def _chat(conv_id: str, *messages: dict) -> None:
    doc = {"id": conv_id, "title": conv_id, "created": 1.0, "updated": 1.0, "skill_snapshot": ""}
    chats.conv_save(PROJECT, doc, messages=list(messages))


def _user(text: str, ts: float) -> dict:
    return {"role": "user", "content": text, "text": text, "ts": ts}


def _ask(conv_id: str) -> str:
    rid, _ = ui_rpc.submit("ask_user", {"conv_id": conv_id, "questions": [{"id": "q"}]})
    return rid


def _open_ids() -> list[str]:
    return [event["request_id"] for event in ui_rpc.pending_questions()]


def test_still_waiting_question_survives_restart_and_skip_does_not_answer(monkeypatch):
    _chat("waits", _user("build it", time.time() - 5))
    rid = _ask("waits")
    asked_at = ui_rpc._pending[rid].asked_at
    _restart(monkeypatch)
    assert _open_ids() == [rid]
    assert not ui_rpc.respond(rid, {"ok": True, "answers": {"q": {"skipped": True}}})
    _restart(monkeypatch)
    assert ui_rpc.question_gate("waits", 0) is True
    assert ui_rpc.pending_question("waits")["since"] == asked_at  # the real ask time, not the restart
    assert ui_rpc.respond(rid, {"ok": True, "answers": {"q": {"text": "go"}}})


def test_stop_drops_the_question_and_wakes_its_caller(monkeypatch):
    rid = _ask("stopped")
    slot = ui_rpc._pending[rid]
    assert ui_rpc.drop_for_conv("stopped") == [rid]
    assert slot.event.is_set() and slot.result["stopped"] is True  # a blocked caller wakes
    assert ui_rpc.question_gate("stopped", 0) is False
    _restart(monkeypatch)
    assert rid not in _open_ids()


def test_newer_turn_in_the_chat_drops_it_after_restart(monkeypatch):
    _chat("moved", _user("first", time.time() - 60))
    rid = _ask("moved")
    _chat("moved", _user("first", time.time() - 60), _user("never mind, do the other thing", (time.time() + 1) * 1000))
    _restart(monkeypatch)
    assert rid not in _open_ids()
    assert ui_rpc.question_gate("moved", 0) is False


def test_deleted_chat_drops_it(monkeypatch):
    _chat("deleted", _user("hi", time.time() - 60))
    rid = _ask("deleted")
    chats.conv_delete("deleted")
    _restart(monkeypatch)
    assert rid not in _open_ids()
