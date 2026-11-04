"""panel_chats.send: send_message passes admission's 'queued' marker through as a friendly
reply, and a rewind refuses a chat that changed."""

from types import SimpleNamespace

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import send


def _send(monkeypatch, run_id: str) -> dict:
    monkeypatch.setattr(_pa, "load_conversation", lambda conv_id: None)
    monkeypatch.setattr(_pa, "run_message", lambda *a, **k: run_id)
    monkeypatch.setattr(send, "_sync_composer_selection", lambda *a, **k: None)
    api = PanelApiChatsMixin()
    api._push = lambda *a, **k: None
    return api.send_message("c1", "hi", "agent", "m")


def test_queued_send_says_sent(monkeypatch) -> None:
    assert _send(monkeypatch, "queued") == {
        "ok": True, "queued": True, "note": "Sent — the agent takes it at its next step"}


def test_started_send_returns_run_id(monkeypatch) -> None:
    assert _send(monkeypatch, "r1") == {"run_id": "r1"}


def test_held_for_a_model_says_so(monkeypatch) -> None:
    from frontend.ui_web.agent_turns.pending import HELD_FOR_MODEL

    assert _send(monkeypatch, HELD_FOR_MODEL) == {"run_id": "", "held_for_model": "1"}


def test_held_messages_need_a_model_first() -> None:
    assert PanelApiChatsMixin().run_held_messages("c", "default") == {"ok": False, "error": "Pick a model first."}


def test_a_rewind_refuses_a_chat_that_changed(monkeypatch) -> None:
    conv = SimpleNamespace(messages=[{"role": "user", "text": "hi"}])
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: conv)
    monkeypatch.setattr(_pa, "list_running_agents", lambda: [])
    got = PanelApiChatsMixin().rewind_to_message("c", 0, "something else")
    assert got == {"ok": False, "error": "This chat changed. Reload it and try again."}
    assert conv.messages == [{"role": "user", "text": "hi"}]
