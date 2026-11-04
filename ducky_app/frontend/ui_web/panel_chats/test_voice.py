"""panel_chats.voice: each voice RPC hands its arguments, as strings, to its backend."""

from __future__ import annotations

import frontend.ui_web.panel_api  # noqa: F401  (loads panel_api_chats without the import cycle)
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


def test_windows_dictation_and_tts_pass_their_arguments(monkeypatch):
    from backend.voice import windows_speech, windows_tts

    seen: list[tuple] = []
    monkeypatch.setattr(windows_speech, "poll_session", lambda s, c, w: seen.append(("poll", s, c, w)) or {})
    monkeypatch.setattr(windows_speech, "start_session", lambda lang: seen.append(("start", lang)) or {})
    monkeypatch.setattr(windows_tts, "start", lambda text, voice: seen.append(("tts", text, voice)) or {})
    api = PanelApiChatsMixin()
    api.voice_win_stt_poll(None, 3, 100)
    api.voice_win_stt_start()
    api.voice_win_tts_start("hi")
    assert seen == [("poll", "", 3, 100), ("start", ""), ("tts", "hi", "")]


def test_a_reply_summary_uses_the_given_model(monkeypatch):
    from backend.voice import summary

    monkeypatch.setattr(summary, "summarize_for_speech", lambda text, model="": {"text": text, "model": model})
    assert PanelApiChatsMixin().voice_summarize_reply("Long reply", "m") == {"text": "Long reply", "model": "m"}
